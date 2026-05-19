import logging
import os
import requests
import time
import json
import asyncio
import aiohttp
import aiofiles
from datetime import datetime, timedelta
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from config import CHANNEL_ID, THUMB_URL,BOT_TEXT
import zipfile
from Extractor.core.utils import forward_to_log
from io import BytesIO

# Constants
MAX_WORKERS = 5000
MAX_RETRIES = 15
TIMEOUT = 90
UPDATE_INTERVAL = 15
SESSION_TIMEOUT = 200

def sort_lines_by_date(lines):
    """Sort output lines by embedded date in ascending order (oldest first).
    Handles both YYYY-MM-DD and DD-MM-YYYY date formats."""
    import re as _re
    from datetime import datetime as _dt
    def _extract_sort_date(line):
        match = _re.search(r'(\d{4})-(\d{2})-(\d{2})', line)
        if match:
            try:
                return _dt.strptime(match.group(0), '%Y-%m-%d')
            except:
                pass
        match = _re.search(r'(\d{2})-(\d{2})-(\d{4})', line)
        if match:
            try:
                day, month, year = match.group(1), match.group(2), match.group(3)
                return _dt.strptime(f"{year}-{month}-{day}", '%Y-%m-%d')
            except:
                pass
        return _dt.min
    return sorted(lines, key=_extract_sort_date)

def sort_and_group_by_subject(lines):
    """Group lines by [subject] prefix, sort subjects by earliest date (ascending),
    sort lines within each subject by date (ascending). Oldest subject first."""
    import re as _re
    from datetime import datetime as _dt

    def _extract_sort_date(line):
        match = _re.search(r'(\d{4})-(\d{2})-(\d{2})', line)
        if match:
            try:
                return _dt.strptime(match.group(0), '%Y-%m-%d')
            except:
                pass
        match = _re.search(r'(\d{2})-(\d{2})-(\d{4})', line)
        if match:
            try:
                day, month, year = match.group(1), match.group(2), match.group(3)
                return _dt.strptime(f"{year}-{month}-{day}", '%Y-%m-%d')
            except:
                pass
        return _dt.min

    # Group by subject from [SubjectName] prefix
    subject_groups = {}
    subject_order = []
    for line in lines:
        match = _re.match(r'\[([^\]]+)\]', line)
        if match:
            subject = match.group(1)
        else:
            subject = "General"
        if subject not in subject_groups:
            subject_groups[subject] = []
            subject_order.append(subject)
        subject_groups[subject].append(line)

    # Sort lines within each subject by date (ascending = oldest first)
    for subject in subject_groups:
        subject_groups[subject] = sorted(subject_groups[subject], key=_extract_sort_date)

    # Sort subjects by their earliest date (ascending = oldest subject first)
    def _subject_earliest_date(subject):
        dates = [_extract_sort_date(line) for line in subject_groups[subject]]
        return min(dates) if dates else _dt.min

    sorted_subjects = sorted(subject_order, key=_subject_earliest_date)

    # Concatenate groups
    result = []
    for subject in sorted_subjects:
        result.extend(subject_groups[subject])

    return result


# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def khan_login(app: Client, message: Message):
    """Start the KGS extraction process."""
    try:
        start_time = time.time()
        
        # Initial message
        editable = await message.reply_text(
            "🔹 <b>KGS EXTRACTOR PRO</b> 🔹\n\n"
            "Send <b>ID & Password</b> in this format: <code>ID*Password</code>"
        )
        
        input1 = await app.listen(chat_id=message.chat.id)
        # After getting user response
        await forward_to_log(input1, "Khan Extractor")
        raw_text = input1.text
        await input1.delete()
        
        if '*' not in raw_text:
            await editable.edit_text(
                "❌ <b>Invalid format!</b>\n\n"
                "Please send ID and password in this format: <code>ID*Password</code>"
            )
            return
            
        user_id, password = raw_text.split('*', 1)
        
        # Setup headers
        headers = {
            "Host": "khanglobalstudies.com",
            "content-type": "application/x-www-form-urlencoded",
            "accept-encoding": "gzip",
            "user-agent": "okhttp/3.9.1",
        }
        
        # Login
        try:
            login_url = "https://khanglobalstudies.com/api/login-with-password"
            data = {
                "phone": user_id,
                "password": password,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(login_url, headers=headers, data=data, timeout=TIMEOUT) as response:
                    if response.status != 200:
                        await editable.edit_text("❌ <b>Login Failed!</b>\n\nPlease check your credentials and try again.")
                        return
                    
                    response_data = await response.json()
                    if "token" not in response_data:
                        await editable.edit_text("❌ <b>Login Failed!</b>\n\nToken not found in response.")
                        return
                        
                    token = response_data["token"]
                    
            # Update headers with token
            headers["authorization"] = f"Bearer {token}"
            
            # Fetch courses
            courses_url = "https://khanglobalstudies.com/api/user/v2/courses"
            async with aiohttp.ClientSession() as session:
                async with session.get(courses_url, headers=headers, timeout=TIMEOUT) as response:
                    if response.status != 200:
                        await editable.edit_text("❌ Failed to fetch courses.")
                        return
                        
                    courses = await response.json()
                    
            if not courses:
                await editable.edit_text("❌ No courses found in your account.")
                return
                
            # Format batch information
            batch_text = ""
            batch_ids = []
            
            for course in courses:
                batch_text += f"<code>{course['id']}</code> - <b>{course['title']}</b> 💰\n\n"
                batch_ids.append(str(course['id']))
                
            await editable.edit_text(
                f"✅ <b>Login Successful!</b>\n\n"
                f"🆔 <b>Credentials:</b> <code>{raw_text}</code>\n\n"
                f"📚 <b>Available Batches:</b>\n\n{batch_text}"
            )
            
            # Ask for batch selection
            input2 = await app.ask(
                message.chat.id,
                f"<b>📥 Send the Batch ID to download</b>\n\n"
                f"<b>💡 For ALL batches:</b> <code>{'&'.join(batch_ids)}</code>\n\n"
                f"<i>Supports multiple IDs separated by '&'</i>"
            )
            
            selected_ids = input2.text.strip().split('&')
            await input2.delete()
            await editable.delete()
            
            # Process each selected batch
            for batch_id in selected_ids:
                batch_id = batch_id.strip()
                progress_msg = await message.reply_text(
                    "🔄 <b>Processing Large Batch</b>\n"
                    f"└─ Initializing batch: <code>{batch_id}</code>"
                )
                
                # Get batch details
                selected_batch = next((b for b in courses if str(b['id']) == batch_id), None)
                if not selected_batch:
                    await progress_msg.edit_text(f"❌ Invalid batch ID: {batch_id}")
                    continue
                    
                # Extract content
                await extract_content(app, message, headers, selected_batch, progress_msg)
                
        except Exception as e:
            logger.error(f"Error in login process: {e}")
            await editable.edit_text(f"❌ <b>Error during login:</b>\n<code>{str(e)}</code>")
            
    except Exception as e:
        logger.error(f"Error in khan_login: {e}")
        await message.reply_text(f"❌ <b>An error occurred:</b>\n<code>{str(e)}</code>")

async def extract_content(app, message, headers, batch, progress_msg):
    """Extract content with improved error handling and workers."""
    try:
        start_time = time.time()
        batch_id = batch['id']
        batch_name = batch['title']
        
        # Fetch lessons
        lessons_url = f"https://khanglobalstudies.com/api/user/courses/{batch_id}/v2-lessons"
        async with aiohttp.ClientSession() as session:
            async with session.get(lessons_url, headers=headers, timeout=TIMEOUT) as response:
                if response.status != 200:
                    await progress_msg.edit_text("❌ Failed to fetch lessons.")
                    return
                lessons = await response.json()
                
        total_lessons = len(lessons)
        if not total_lessons:
            await progress_msg.edit_text("❌ No lessons found in this batch.")
            return
            
        # Initialize content storage
        all_urls = []
        topic_wise_content = {}
        
        # Process lessons with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for lesson in lessons:
                future = executor.submit(
                    process_lesson,
                    lesson,
                    headers,
                    TIMEOUT
                )
                futures.append(future)
                
            # Process results
            completed = 0
            for future in as_completed(futures):
                try:
                    lesson_content = future.result()
                    if lesson_content:
                        lesson_name, urls, topic_content = lesson_content
                        all_urls.extend(urls)
                        if topic_content:  # Only add if there's content
                            topic_wise_content[lesson_name] = topic_content
                        
                    completed += 1
                    if completed % 3 == 0:
                        await progress_msg.edit_text(
                            f"🔄 <b>Processing Large Batch</b>\n"
                            f"├─ Progress: {completed}/{total_lessons} lessons\n"
                            f"├─ Links found: {len(all_urls)}\n"
                            f"└─ Please wait..."
                        )
                except Exception as e:
                    logger.error(f"Error processing lesson: {e}")
                    continue
                    
        if not all_urls:
            await progress_msg.edit_text("❌ No content found in this batch.")
            return
            
        # Create simple txt file with URLs
        txt_filename = f"KGS_{batch_name}_{int(time.time())}.txt"
        
        # Sort URLs by date in ascending order (oldest first)
        all_urls = sort_and_group_by_subject(all_urls)
        
        async with aiofiles.open(txt_filename, 'w', encoding='utf-8') as f:
            await f.write('\n'.join(all_urls))
            
        # Create zip file with topic-wise content
        zip_filename = f"KGS_{batch_name}_{int(time.time())}_topics.zip"
        try:
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for topic, content in topic_wise_content.items():
                    # Sanitize topic name for filename
                    safe_topic = "".join(x for x in topic if x.isalnum() or x in (' ', '-', '_')).strip()
                    topic_filename = f"{safe_topic}.txt"
                    zipf.writestr(topic_filename, '\n'.join(content))
        except Exception as e:
            logger.error(f"Error creating ZIP file: {e}")
            # Continue without ZIP if there's an error
            
        # Count content types based on URLs
        video_count = sum(1 for url in all_urls if any(ext in url.lower() for ext in ['.mp4', '.m3u8', '.mpd', 'youtu.be', 'youtube.com', '/videos/', '/video/']))
        pdf_count = sum(1 for url in all_urls if '.pdf' in url.lower())
        other_count = len(all_urls) - (video_count + pdf_count)
        
        # Prepare caption
        duration = time.time() - start_time
        minutes, seconds = divmod(duration, 60)
        
        caption = (
            f"🎓 <b>COURSE EXTRACTED</b> 🎓\n\n"
            f"📱 <b>APP:</b> Khan GS\n"
            f"📚 <b>BATCH:</b> {batch_name} (ID: {batch_id})\n"
            f"⏱ <b>EXTRACTION TIME:</b> {int(minutes):02d}:{int(seconds):02d}\n"
            f"📅 <b>DATE:</b> {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d-%m-%Y %H:%M:%S')} IST\n\n"
            f"📊 <b>CONTENT STATS</b>\n"
            f"├─ 📁 Total Links: {len(all_urls)}\n"
            f"├─ 🎬 Videos: {video_count}\n"
            f"├─ 📄 PDFs: {pdf_count}\n"
            f"├─ 📦 Others: {other_count}\n"
            f"└─ 📚 Topics: {len(topic_wise_content)}\n\n"
            f"🚀 <b>Extracted by:</b> @{(await app.get_me()).username}\n\n"
            f"<code>╾───• {BOT_TEXT} •───╼</code>"
        )
        
        # Send files
        try:
            # Send simple txt file
            await app.send_document(
                message.chat.id,
                document=txt_filename,
                caption=f"{caption}\n\n💡 This file contains simple URL list",
                parse_mode=ParseMode.HTML
            )
            await app.send_document(CHANNEL_ID, document=txt_filename, caption=caption)
            
            # Send zip file if it was created successfully
            if os.path.exists(zip_filename):
                await app.send_document(
                    message.chat.id,
                    document=zip_filename,
                    caption=f"{caption}\n\n💡 This ZIP contains topic-wise content",
                    parse_mode=ParseMode.HTML
                )
                await app.send_document(CHANNEL_ID, document=zip_filename, caption=caption)
            
            # Final status
            await progress_msg.edit_text(
                "✅ <b>Extraction completed successfully!</b>\n\n"
                f"📊 <b>Final Status:</b>\n"
                f"├─ Processed: {total_lessons} lessons\n"
                f"├─ Total Links: {len(all_urls)}\n"
                f"└─ Files sent: ✅\n\n"
                f"Thank you for using KGS Extractor! 🌟"
            )
            
        except Exception as e:
            logger.error(f"Error sending files: {e}")
            await progress_msg.edit_text(f"❌ Error sending files: {str(e)}")
            
        finally:
            # Cleanup
            try:
                os.remove(txt_filename)
                if os.path.exists(zip_filename):
                    os.remove(zip_filename)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Error in extract_content: {e}")
        await progress_msg.edit_text(
            "❌ <b>An error occurred during extraction</b>\n\n"
            f"Error details: <code>{str(e)}</code>\n\n"
            "Please try again or contact support."
        )

def extract_date(item):
    """Extract and format date from API response item"""
    for field in ['createdAt', 'created_at', 'date', 'startTime', 'updatedAt', 'updated_at', 'createdDate', 'publishedOn']:
        val = item.get(field)
        if val:
            try:
                from datetime import datetime as dt
                if isinstance(val, (int, float)):
                    if val > 1e12:
                        val = val / 1000
                    return dt.fromtimestamp(val).strftime('%d-%m-%Y')
                elif isinstance(val, str):
                    for fmt in ['%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
                        try:
                            return dt.strptime(val[:26], fmt).strftime('%d-%m-%Y')
                        except:
                            continue
                    if len(val) >= 10:
                        return val[:10]
            except:
                pass
    return ""

def process_lesson(lesson, headers, timeout):
    """Process a single lesson (runs in thread)."""
    try:
        lesson_name = lesson.get('name', 'Untitled Lesson')
        lesson_id = lesson.get('id')
        if not lesson_id:
            return None
            
        # Get lesson details
        lesson_url = f"https://khanglobalstudies.com/api/lessons/{lesson_id}"
        response = requests.get(lesson_url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
            
        lesson_data = response.json()
        urls = []
        topic_content = []
        
        # Process videos
        videos = lesson_data.get('videos', [])
        for video in videos:
            name = video.get('name', 'Untitled Video')
            url = video.get('video_url', '')
            if url:
                vid_date = extract_date(video); date_str = f"{vid_date} " if vid_date else ""; urls.append(f"[{lesson_name}] {date_str}{name}: {url}")
                topic_content.append(f"🎬 {name}\n{url}")
                
        # Process PDFs/Notes
        notes = lesson_data.get('notes', [])
        for note in notes:
            name = note.get('name', 'Untitled Note')
            url = note.get('url', '')
            if url:
                note_date = extract_date(note); date_str = f"{note_date} " if note_date else ""; urls.append(f"[{lesson_name}] {date_str}{name}: {url}")
                topic_content.append(f"📄 {name}\n{url}")
                
        return lesson_name, urls, topic_content
        
    except Exception as e:
        logger.error(f"Error processing lesson: {e}")
        return None