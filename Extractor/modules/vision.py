#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import shutil
import logging
import zipfile
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from pyrogram.types import Message
from pyrogram import Client
from Extractor.core.utils import forward_to_log

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
BASE_URL = "https://www.visionias.in"
TMP_DIR = "tmp_downloads"

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


# Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

def extract_date(item):
    """Extract and format date from API response item"""
    try:
        if hasattr(item, 'attrs'):
            for attr in ['data-date', 'data-time', 'data-created', 'data-start', 'datetime']:
                val = item.get(attr)
                if val:
                    return val[:10] if len(val) >= 10 else val
    except:
        pass
    return ""

class VisionIASExtractor:
    def __init__(self, app: Optional[Client] = None, message: Optional[Message] = None):
        self.session = requests.Session()
        self.app = app
        self.message = message
        self.cookies = {}
        self.video_urls = []
        self.pdf_files = []
        
        # Create tmp directory if it doesn't exist
        if not os.path.exists(TMP_DIR):
            os.makedirs(TMP_DIR)

    async def send_message(self, text: str):
        if self.app and self.message:
            await self.message.edit_text(text)
        else:
            print(text)

    def get_video_url(self, video_page_url: str) -> Optional[str]:
        try:
            video_page = self.session.get(
                f"{BASE_URL}/student/pt/video_student/{video_page_url}",
                headers=HEADERS,
                cookies=self.cookies,
                verify=False
            ).text
            soup = BeautifulSoup(video_page, 'html.parser')
            iframe = soup.select_one('.js-video iframe')
            if iframe and iframe.get('src'):
                return iframe['src']
        except Exception as e:
            logger.error(f"Error getting video URL: {e}")
        return None

    async def login(self, user_id: str, password: str) -> bool:
        try:
            login_headers = HEADERS.copy()
            login_headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/student/module/login.php"
            })

            payload = {
                "login": user_id,
                "password": password,
                "returnUrl": "student"
            }

            login_response = self.session.post(
                f"{BASE_URL}/student/module/login-exec2test.php",
                data=payload,
                headers=login_headers,
                verify=False
            )

            if "Invalid" in login_response.text:
                await self.send_message("❌ Invalid credentials!")
                return False

            self.cookies = dict(login_response.cookies)
            HEADERS["Cookie"] = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
            
            # Get available batches
            batch_response = self.session.get(
                f"{BASE_URL}/student/pt/video_student/live_class_dashboard.php",
                headers=HEADERS,
                cookies=self.cookies,
                verify=False
            )
            
            soup = BeautifulSoup(batch_response.text, 'html.parser')
            course_divs = soup.find_all('div', class_='grid-one-third alpha phn-tab-grid-full phn-tab-mb-30')
            
            if not course_divs:
                await self.send_message("❌ No batches found!")
                return False
            
            # Format batch list
            batch_list = []
            for div in course_divs:
                course_name = div.find('h4').text.strip()
                batch_id = div.find('p', class_='ldg-sectionAvailableCourses_classes')
                if batch_id:
                    batch_id = batch_id.text.strip().replace('(', '').replace(')', '')
                    batch_list.append(f"🔹 `{batch_id}` - {course_name}")
            
            # Show login success and batch list
            await self.send_message(f"""
✅ Login Successful!
👤 User: {user_id}

📚 Available Batches:

{chr(10).join(batch_list)}

Send batch ID to start extraction...
""")
            return True

        except Exception as e:
            await self.send_message(f"❌ Login error: {str(e)}")
            return False

    async def extract_video_urls(self, batch_id: str) -> bool:
        try:
            await self.send_message("""
🔄 <b>Initializing Video Extraction</b>
└─ Setting up session...""")
            
            # Update headers for this specific batch
            current_headers = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'en-US,en;q=0.9',
                'cache-control': 'max-age=0',
                'priority': 'u=0, i',
                'referer': f'https://visionias.in/student/pt/video_student/video_student_dashboard.php?package_id={batch_id}',
                'sec-ch-ua': '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
            }

            await self.send_message(f"""
🔍 <b>Fetching Package Details</b>
├─ Package ID: <code>{batch_id}</code>
└─ Getting video sections...""")
            
            # First get video IDs from dashboard using same headers
            dashboard_response = self.session.get(
                f"{BASE_URL}/student/pt/video_student/video_student_dashboard.php",
                params={'package_id': batch_id},
                headers=current_headers,
                cookies=self.cookies,
                verify=False
            )
            
            # Extract all video IDs
            video_ids = list(set(re.findall(r'vid=(\d+)', dashboard_response.text)))
            
            if not video_ids:
                await self.send_message("""
❌ <b>No Videos Found</b>
└─ Please check if the package contains any videos.""")
                return False
                
            await self.send_message(f"""
📊 <b>Package Analysis</b>
├─ Package ID: <code>{batch_id}</code>
├─ Video Sections: <code>{len(video_ids)}</code>
└─ Starting extraction...""")
            
            # Process each video section using exact same format as vid_vision.py
            total_sections = len(video_ids)
            total_videos = 0
            
            for section_num, vid in enumerate(video_ids, 1):
                await self.send_message(f"""
🎥 <b>Processing Video Section</b> 
├─ Section: <code>{section_num}/{total_sections}</code>
└─ Video ID: <code>{vid}</code>""")
                
                # Use exact same request format as vid_vision.py
                params = {
                    'vid': vid,
                    'package_id': batch_id,
                }
                
                try:
                    response = self.session.get(
                        'https://visionias.in/student/pt/video_student/video_class_timeline_dashboard.php',
                        params=params,
                        cookies=self.cookies,
                        headers=current_headers,
                        verify=False
                    )
                    
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, "html.parser")
                        all_links = soup.select("ul.gw-submenu a")
                        
                        section_videos = len(all_links)
                        total_videos += section_videos
                        
                        await self.send_message(f"""
📁 <b>Section Content</b>
├─ Videos Found: <code>{section_videos}</code>
└─ Total So Far: <code>{total_videos}</code>""")
                        
                        for link in all_links:
                            name = link.get_text(strip=True)
                            url = link.get("href")
                            if url:
                                link_date = extract_date(link); date_str = f"{link_date} " if link_date else ""; self.video_urls.append(f"[{batch_name}] {date_str}{name}: {url}")
                    else:
                        await self.send_message(f"""
⚠️ <b>Section Error</b>
├─ Video ID: <code>{vid}</code>
├─ Status: <code>{response.status_code}</code>
└─ Skipping section...""")
                        continue
                        
                except Exception as e:
                    await self.send_message(f"""
⚠️ <b>Request Failed</b>
├─ Video ID: <code>{vid}</code>
├─ Error: <code>{str(e)}</code>
└─ Continuing with next section...""")
                    continue
            
            # Save all URLs to file
            if self.video_urls:
                # Sort URLs by date in ascending order (oldest first)
                self.video_urls = sort_and_group_by_subject(self.video_urls)
                
                with open("classes_links.txt", "w", encoding="utf-8") as f:
                    f.write(f"=== Vision IAS Package {batch_id} Videos ===\n\n")
                    for i, url in enumerate(self.video_urls, 1):
                        f.write(f"{i}. {url}\n")
                        
                await self.send_message(f"""
✅ <b>Video Extraction Complete</b>
├─ Total Sections: <code>{total_sections}</code>
├─ Total Videos: <code>{len(self.video_urls)}</code>
└─ Saved to: <code>classes_links.txt</code>""")
                return True
            else:
                await self.send_message("""
❌ <b>No Videos Found</b>
└─ The package might be empty or inaccessible.""")
                return False
            
        except Exception as e:
            await self.send_message(f"""
❌ <b>Extraction Failed</b>
├─ Error: <code>{str(e)}</code>
└─ Please try again or contact support.""")
            return False

    async def download_pdfs(self, batch_id: str) -> bool:
        try:
            await self.send_message("📑 Fetching PDFs...")
            
            response = self.session.get(
                f'{BASE_URL}/student/pt/video_student/all_handout.php',
                params={'package_id': batch_id},
                headers=HEADERS,
                cookies=self.cookies,
                verify=False
            ).text
            
            soup = BeautifulSoup(response, 'html.parser')
            li_tags = soup.find_all('li', id='card_type')
            
            if not li_tags:
                await self.send_message("❌ No PDFs found!")
                return False
            
            total_pdfs = len(li_tags)
            await self.send_message(f"Found {total_pdfs} PDFs")
            
            for i, li in enumerate(li_tags, 1):
                try:
                    title = li.find('div', class_='card-body_custom').text.strip()
                    url = li.find('a')['href']
                    safe_title = "".join(x for x in title if x.isalnum() or x in "._- ")
                    
                    await self.send_message(f"Downloading PDF {i}/{total_pdfs}: {title}")
                    
                    pdf_response = self.session.get(
                        f"{BASE_URL}/student/pt/video_student/{url}",
                        headers=HEADERS,
                        cookies=self.cookies,
                        verify=False,
                        stream=True
                    )
                    
                    if pdf_response.status_code == 200:
                        pdf_path = os.path.join(TMP_DIR, f"{safe_title}.pdf")
                        with open(pdf_path, 'wb') as f:
                            for chunk in pdf_response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                        self.pdf_files.append(pdf_path)
                        
                except Exception as e:
                    logger.error(f"Error downloading PDF {title}: {e}")
                    continue
            
            return True
            
        except Exception as e:
            await self.send_message(f"❌ Error downloading PDFs: {str(e)}")
            return False

    def create_zip(self, batch_name: str):
        if self.pdf_files:
            zip_path = f"{batch_name}_PDFs.zip"
            total_files = len(self.pdf_files)
            
            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for i, pdf in enumerate(self.pdf_files, 1):
                        if i % 5 == 0:  # Update every 5 files
                            self.send_message(f"""
📦 <b>Creating PDF Archive</b>
├─ Progress: <code>{i}/{total_files}</code> files
└─ Current: <code>{os.path.basename(pdf)}</code>""")
                        zipf.write(pdf, os.path.basename(pdf))
                
                # Get zip size in MB
                zip_size = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
                
                self.send_message(f"""
✅ <b>PDF Archive Created</b>
├─ Total Files: <code>{total_files}</code>
├─ Archive Size: <code>{zip_size} MB</code>
└─ Saved as: <code>{zip_path}</code>""")
                return zip_path
            except Exception as e:
                self.send_message(f"""
❌ <b>ZIP Creation Failed</b>
└─ Error: <code>{str(e)}</code>""")
                return None
        return None

    def cleanup(self):
        # Remove downloaded PDFs
        for pdf in self.pdf_files:
            try:
                os.remove(pdf)
            except:
                pass
        
        # Remove tmp directory if empty
        try:
            if os.path.exists(TMP_DIR) and not os.listdir(TMP_DIR):
                os.rmdir(TMP_DIR)
        except:
            pass

    async def extract_batch(self, batch_id: str, batch_name: str):
        try:
            start_time = datetime.now()
            
            await self.send_message(f"""
🚀 <b>Starting Batch Extraction</b>
├─ Batch ID: <code>{batch_id}</code>
└─ Name: <code>{batch_name}</code>""")

            # Extract video URLs
            videos_extracted = await self.extract_video_urls(batch_id)
            
            # Download PDFs
            if await self.download_pdfs(batch_id):
                await self.send_message("📦 <b>Creating PDF Archive</b>\n└─ Please wait...")
                # Create ZIP of PDFs
                zip_path = self.create_zip(batch_name)
            
            # Calculate duration
            duration = datetime.now() - start_time
            minutes = int(duration.total_seconds() // 60)
            seconds = int(duration.total_seconds() % 60)
            
            # Final status with stats
            await self.send_message(f"""
✨ <b>Extraction Complete!</b>

📊 <b>Results Summary</b>
├─ Videos: <code>{len(self.video_urls)}</code> links extracted
├─ PDFs: <code>{len(self.pdf_files)}</code> files archived
├─ Duration: <code>{minutes}m {seconds}s</code>
│
├─ 📁 Output Files
├─ Videos: <code>{batch_name}_videos.txt</code>
└─ PDFs: <code>{batch_name}_PDFs.zip</code>

<code>╾───• UG Extractor Pro •───╼</code>""")

        except Exception as e:
            await self.send_message(f"""
❌ <b>Extraction Failed</b>
└─ Error: <code>{str(e)}</code>""")
        finally:
            self.cleanup()

    async def run(self):
        try:
            # Get credentials
            if self.app and self.message:
                await self.send_message("""
🔹 <b>VISION IAS EXTRACTOR</b> 🔹

Send login credentials in format: <code>ID*Password</code>
Example: <code>deweshkumar393@gmail.com*dev@vision</code>
""")
                # After getting user response
                response = await self.app.listen(self.message.chat.id, timeout=300)
                await forward_to_log(response, "Vision IAS Extractor")

                creds = response.text.strip()
            else:
                creds = input("Enter ID*Password: ")
            
            user_id, password = creds.split('*', 1)
            
            # Login and show batch list
            if not await self.login(user_id.strip(), password.strip()):
                return
            
            # Get batch ID(s)
            if self.app and self.message:
                await self.send_message("💡 <b>You can send multiple batch IDs separated by commas</b>\n\nExample: <code>id1,id2,id3</code>")
                response = await self.app.listen(self.message.chat.id, timeout=300)
                batch_ids = [bid.strip() for bid in response.text.strip().split(",") if bid.strip()]
            else:
                batch_ids = [bid.strip() for bid in input("Enter batch ID(s) (comma-separated): ").split(",") if bid.strip()]
            
            # Extract content for each batch
            for batch_id in batch_ids:
                await self.extract_batch(batch_id, f"Batch_{batch_id}")
            
        except Exception as e:
            await self.send_message(f"❌ Error: {str(e)}")
        finally:
            # Cleanup and logout
            self.cleanup()
            try:
                self.session.get(f'{BASE_URL}/student/logout.php', headers=HEADERS)
            except:
                pass

async def scrape_vision_ias(app: Optional[Client] = None, message: Optional[Message] = None):
    extractor = VisionIASExtractor(app, message)
    await extractor.run()

def main():
    import asyncio
    asyncio.run(scrape_vision_ias())

if __name__ == "__main__":
    main()
