import flet as ft
import sys
import io
import os
import threading
from pathlib import Path

# --- TOOL 1: YOUTUBE UPLOADER IMPORTS (Unchanged) ---
import google_auth_oauthlib.flow
import googleapiclient.discovery
import googleapiclient.errors
from googleapiclient.http import MediaFileUpload

# --- TOOL 2: COURSE DOWNLOADER IMPORTS (Unchanged) ---
import json
import requests
import subprocess

# --- GLOBAL CONFIGURATIONS (Unchanged) ---
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRETS_FILE = "client.json"
BASE_PRESIGNED_URL = "https://api.empire.io.vn/api/v1/files/presigned/"
BASE_DOC_URL = "https://api.empire.io.vn/api/v1/files/"
EXPIRY_SECONDS = 14400

# --- SHARED FLET LOGGER CLASS (Unchanged) ---
class ConsoleLogger(io.TextIOBase):
    def __init__(self, text_field: ft.TextField, page: ft.Page):
        self.log_field, self.page, self.original_stdout = text_field, page, sys.stdout
    def write(self, text: str):
        if not text.strip(): return
        self.log_field.value += text + "\n"
        if self.log_field.visible: self.page.update()
        self.original_stdout.write(text)
    def flush(self): self.original_stdout.flush()

# --- HELPER FUNCTIONS ---
def authenticate_youtube():
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    if not os.path.exists(CLIENT_SECRETS_FILE): print(f"FATAL: {CLIENT_SECRETS_FILE} not found."); return None
    try:
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, YOUTUBE_SCOPES)
        credentials = flow.run_local_server(port=0)
        return googleapiclient.discovery.build("youtube", "v3", credentials=credentials)
    except Exception as e: print(f"❌ YouTube Auth Error: {e}"); return None

def list_youtube_channels(youtube):
    try:
        req = youtube.channels().list(part="snippet,id", mine=True, maxResults=50)
        return [{'id': c['id'], 'title': c['snippet']['title']} for c in req.execute().get('items', [])]
    except Exception as e: print(f"❌ Error fetching YouTube channels: {e}"); return []

def check_video_exists(youtube, channel_id, title):
    try:
        search_response = youtube.search().list(q=f'"{title}"', part='snippet', channelId=channel_id, type='video', maxResults=5).execute()
        for item in search_response.get('items', []):
            if item['snippet']['title'] == title:
                return True
        return False
    except Exception as e:
        print(f"  ⚠️ Warning: Could not check for existing video due to API error: {e}")
        return False

# --- MODIFICATION: upload_video now accepts a privacy_status ---
def upload_video(youtube, cid, path, title, desc, ring, btn, privacy_status='private'):
    try:
        ring.visible = True; btn.disabled = True; page.update()
        body = {'snippet': {'title': title, 'description': desc, 'channelId': cid}, 'status': {'privacyStatus': privacy_status}}
        req = youtube.videos().insert(part=",".join(body.keys()), body=body, media_body=MediaFileUpload(path, chunksize=-1, resumable=True))
        res = None
        while res is None:
            status, res = req.next_chunk()
            if status: ring.value = status.progress(); print(f"  Uploading... {int(ring.value*100)}%"); page.update()
        print(f"  ✅ YouTube Upload successful! Video ID: {res.get('id')}")
        return True
    except Exception as e: print(f"  ❌ YouTube Upload Error: {e}"); return False
    finally: ring.visible = False; btn.disabled = False; page.update()

def sanitize_filename(name):
    s = "".join(c if c.isalnum() or c in (' ', '.', '_', '-', '[', ']') else '_' for c in name)
    return ' '.join(s.split()).strip()[:150]

def _get_course_structure(json_file, output_dir=None):
    try:
        with open(json_file, 'r', encoding='utf-8') as f: course_data = json.load(f).get('course')
        if not course_data: raise ValueError("Invalid JSON: 'course' key not found.")
        course_title_raw = course_data.get('title', 'Unknown Course')
        course_title_sanitized = sanitize_filename(output_dir or course_title_raw)
        tasks = []
        for i, chap in enumerate(course_data.get('chapters', [])):
            chap_title = sanitize_filename(f"{i+1:02d} - {chap.get('title', 'Chap')}")
            for j, less in enumerate(chap.get('lessons', [])):
                less_info = less.get('lesson', {})
                less_title_sanitized = sanitize_filename(f"{j+1:02d} - {less_info.get('title', 'Less')}")
                less_title_raw = less_info.get('title', 'Less')
                for res_item in sorted(less_info.get('resources', []), key=lambda x: x.get('order', 99)):
                    res, file_url = res_item.get('resource', {}), res_item.get('resource', {}).get('fileUrl')
                    if not file_url: continue
                    ext, sub_dir = ('.mp4', "Videos") if res.get('type') == 'video' else ('.pdf', "TaiLieu") if res.get('type') == 'document' else (None, None)
                    if not ext: continue
                    original_title_from_json = res.get('title', 'file')
                    original_title_stem = Path(original_title_from_json).stem
                    new_filename_base = f"{original_title_stem} - [{less_title_raw}]"
                    filename = sanitize_filename(new_filename_base) + ext
                    save_directory = os.path.join(course_title_sanitized, chap_title, less_title_sanitized, sub_dir)
                    display_path = os.path.join(chap_title, less_title_sanitized, sub_dir, filename)
                    tasks.append({'id': file_url, 'type': res.get('type'), 'file_url_part': file_url, 'save_dir': save_directory, 'filename': filename, 'display_path': display_path})
        return course_title_sanitized, tasks, course_title_raw
    except Exception as e:
        print(f"❌ Error processing JSON file: {e}")
        return None, None, None

def get_presigned_url(file_url, headers):
    try:
        res = requests.get(f"{BASE_PRESIGNED_URL}{file_url}?expirySeconds={EXPIRY_SECONDS}", headers=headers)
        res.raise_for_status(); return res.json().get('url')
    except Exception as e: print(f"❌ Presigned URL Error: {e}"); return None
def download_with_aria2(url, directory, filename):
    os.makedirs(directory, exist_ok=True)
    cmd = ['aria2c','--console-log-level=warn','--summary-interval=0','-x16','-s16','-k1M','--dir',directory,'--out',filename,url]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        p.communicate(); return p.returncode == 0
    except FileNotFoundError: print("❌ CRITICAL: 'aria2c' not found."); return False
    except Exception as e: print(f"❌ aria2c Error: {e}"); return False

# --- MAIN FLET APPLICATION ---
def main(page: ft.Page):
    page.title = "Multi-Tool App"
    page.window_width = 950
    page.window_height = 850
    page.session.set("youtube_auth_thread_id", 0); page.session.set("youtube_client", None); page.session.set("selected_channel_id", None)
    page.session.set("selected_channel_title", "None"); page.session.set("video_to_upload_path", None); page.session.set("downloader_json_path", None)
    page.session.set("bulk_upload_folder_path", None)
    page.session.set("downloader_should_stop", False)

    # --- Worker Threads ---
    def youtube_auth_worker(thread_id):
        if thread_id != page.session.get("youtube_auth_thread_id"): return
        youtube_client = authenticate_youtube()
        if thread_id != page.session.get("youtube_auth_thread_id"): return
        if youtube_client:
            page.session.set("youtube_client", youtube_client); channels = list_youtube_channels(youtube_client)
            if channels: show_channel_dialog(channels)
            else: stop_youtube_auth_flow(None)
        else: stop_youtube_auth_flow(None)
        page.update()

    def youtube_upload_worker():
        path = page.session.get("video_to_upload_path")
        cid = page.session.get("selected_channel_id")
        yt = page.session.get("youtube_client")
        title = youtube_title_field.value
        # --- MODIFICATION: Get privacy status from dropdown ---
        privacy = youtube_privacy_dropdown.value or 'private'
        
        if not all([path, title, cid, yt]): 
            print("❌ Error: Missing video, title, or channel.")
            youtube_upload_button.disabled = False
            page.update()
            return
        upload_video(yt, cid, path, title, youtube_description_field.value, youtube_upload_progress_ring, youtube_upload_button, privacy_status=privacy)

    # --- MODIFICATION: Bulk uploader now searches subfolders and accepts privacy status ---
    def bulk_upload_worker(folder_path, start_button, privacy_status):
        try:
            yt, cid = page.session.get("youtube_client"), page.session.get("selected_channel_id")
            if not all([yt, cid]):
                print("❌ Error: Please authenticate and select a channel first.")
                return

            print(f"Scanning folder '{folder_path}' and all subfolders for videos...")
            video_paths = []
            valid_extensions = (".mp4", ".mov", ".mkv", ".avi", ".webm")
            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith(valid_extensions):
                        video_paths.append(os.path.join(root, file))

            if not video_paths:
                print("No video files found in the selected directory or its subfolders.")
                return
            
            total_videos = len(video_paths)
            print(f"Found {total_videos} videos. Starting bulk process with privacy set to '{privacy_status}'...")
            
            for i, video_path in enumerate(video_paths):
                video_title = Path(video_path).stem
                print(f"\n--- ({i+1}/{total_videos}) Processing: {video_path} ---")
                
                print(f"  Checking for existing video with title: '{video_title}'...")
                if check_video_exists(yt, cid, video_title):
                    print(f"  ⏩ Already uploaded. Skipping video.")
                    continue

                print(f"  Video not found on channel. Starting upload...")
                if not upload_video(yt, cid, video_path, video_title, f"Uploaded via Flet Bulk Uploader.", youtube_upload_progress_ring, youtube_upload_button, privacy_status=privacy_status):
                    print(f"  ❌ Stopping bulk upload due to an error with '{Path(video_path).name}'."); break
            
            print("\n--- Bulk upload process finished. ---")
        except Exception as e:
            print(f"❌ A critical error occurred during bulk upload: {e}")
        finally:
            start_button.disabled = False
            page.update()

    def downloader_worker(json_path, token, output_dir, start_button, stop_button, progress_bar):
        try:
            if not token: print("[Lỗi] Bearer Token là bắt buộc."); return
            headers, (course_title, download_tasks, original_course_title) = {'Authorization': f'Bearer {token}' if not token.lower().startswith('bearer ') else token}, _get_course_structure(json_path, output_dir)
            if not download_tasks: print("Could not find any tasks in the JSON."); return
            os.makedirs(course_title, exist_ok=True)
            print(f"Bắt đầu tải khóa học: {original_course_title}")
            print(f"Đã tìm thấy tổng cộng {len(download_tasks)} file để tải.")
            progress_bar.visible = True; page.update()
            for i, task in enumerate(download_tasks):
                if page.session.get("downloader_should_stop"):
                    print("\n🛑 Quá trình tải đã được người dùng dừng lại.")
                    break
                progress_bar.value = (i + 1) / len(download_tasks)
                print(f"Đang xử lý {i+1}/{len(download_tasks)}: {task['display_path']}")
                page.update()
                download_url = get_presigned_url(task['file_url_part'], headers) if task['type'] == 'video' else f"{BASE_DOC_URL}{task['file_url_part']}"
                if download_url:
                    if not download_with_aria2(download_url, task['save_dir'], task['filename']):
                        print(f"  Bỏ qua file '{task['filename']}' do lỗi tải.")
                else:
                    print(f"  Bỏ qua file '{task['filename']}' do không lấy được URL.")
            else:
                print("\nTải khóa học hoàn tất!")
        except Exception as e: print(f"❌ Lỗi nghiêm trọng trong downloader: {e}")
        finally:
            page.session.set("downloader_should_stop", False); start_button.visible = True; stop_button.visible = False
            downloader_manage_log_button.disabled = False; start_button.disabled = False; progress_bar.visible = False
            page.update()

    # --- UI Event Handlers ---
    def start_youtube_auth_flow(e):
        id = page.session.get("youtube_auth_thread_id") + 1; page.session.set("youtube_auth_thread_id", id)
        youtube_auth_button.visible = False; youtube_stop_button.visible = True; page.update()
        threading.Thread(target=youtube_auth_worker, args=(id,), daemon=True).start()
    
    def stop_youtube_auth_flow(e, success=False):
        if not success: print("YouTube auth cancelled.")
        youtube_auth_button.visible = True; youtube_stop_button.visible = False; page.update()

    def start_youtube_upload_flow(e): e.control.disabled = True; page.update(); threading.Thread(target=youtube_upload_worker, daemon=True).start()
    def on_youtube_video_picked(e: ft.FilePickerResultEvent):
        if e.files: path = e.files[0].path; page.session.set("video_to_upload_path", path); youtube_selected_file_text.value = f"Selected: {Path(path).name}"
        else: page.session.set("video_to_upload_path", None); youtube_selected_file_text.value = "No video selected."
        page.update()

    def show_channel_dialog(channels):
        def channel_selected(e):
            cid, title = e.control.data, [c['title'] for c in channels if c['id'] == e.control.data][0]
            page.session.set("selected_channel_id", cid); page.session.set("selected_channel_title", title)
            print(f"✅ YouTube Channel Selected: {title}"); youtube_selected_channel_text.value = f"Target: {title}"
            channel_dialog.open = False; stop_youtube_auth_flow(None, success=True); page.update()
        channel_dialog.content = ft.Column([ft.ListTile(title=ft.Text(c['title']), on_click=channel_selected, data=c['id']) for c in channels], tight=True, scroll=True)
        channel_dialog.open = True; page.update()

    def on_bulk_folder_picked(e: ft.FilePickerResultEvent):
        if e.path: page.session.set("bulk_upload_folder_path", e.path); bulk_upload_folder_text.value = f"Selected: {e.path}"
        else: page.session.set("bulk_upload_folder_path", None); bulk_upload_folder_text.value = "No folder selected."
        page.update()

    def start_bulk_upload_flow(e):
        folder_path = page.session.get("bulk_upload_folder_path")
        if not folder_path:
            print("❌ Error: Please select a folder first.")
            return
        # --- MODIFICATION: Get privacy status and pass it to the worker ---
        privacy = bulk_privacy_dropdown.value or 'private'
        e.control.disabled = True
        page.update()
        threading.Thread(target=bulk_upload_worker, args=(folder_path, e.control, privacy), daemon=True).start()

    def start_downloader_flow(e):
        token, json_path = downloader_token_field.value, page.session.get("downloader_json_path")
        if not token or not json_path: print("❌ Error: Token and JSON file are required."); return
        page.session.set("downloader_should_stop", False); e.control.visible = False; downloader_stop_button.visible = True
        downloader_manage_log_button.disabled = True; page.update()
        threading.Thread(target=downloader_worker, args=(json_path, token, downloader_output_dir_field.value, e.control, downloader_stop_button, downloader_progress_bar), daemon=True).start()
    def on_downloader_json_picked(e: ft.FilePickerResultEvent):
        if e.files: path = e.files[0].path; page.session.set("downloader_json_path", path); downloader_selected_json_text.value = f"Selected: {Path(path).name}"
        else: page.session.set("downloader_json_path", None); downloader_selected_json_text.value = "No file selected."
        page.update()
    def stop_downloader_flow(e):
        print("Stop command received. The downloader will stop after the current file...")
        page.session.set("downloader_should_stop", True); e.control.disabled = True; page.update()
    def open_log_manager(e):
        json_path=page.session.get("downloader_json_path");
        if not json_path: print("❌ Select a JSON file first."); return
        def save_log(e_save):
            course_title,_,_= _get_course_structure(json_path, downloader_output_dir_field.value)
            log_file=os.path.join(course_title,".download_log.txt"); new_ids={cb.data for cb in log_manager_dialog.content.controls if cb.value}
            with open(log_file,'w',encoding='utf-8') as f:
                for item_id in sorted(list(new_ids)): f.write(f"{item_id}\n")
            print(f"✅ Log file saved!"); log_manager_dialog.open = False; page.update()
        log_manager_dialog.content=ft.Row([ft.ProgressRing()]); log_manager_dialog.actions=[ft.ElevatedButton("Save",on_click=save_log),ft.TextButton("Cancel",on_click=lambda _:setattr(log_manager_dialog,'open',False) or page.update())]
        log_manager_dialog.open=True; page.update()
        def setup_worker(jp,o,d):
            _,tasks,_=_get_course_structure(jp,o)
            if not tasks: d.content=ft.Text("No tasks found."); page.update(); return
            log_f=os.path.join(sanitize_filename(o or _get_course_structure(jp)[2]),".download_log.txt"); ids=set()
            if os.path.exists(log_f):
                with open(log_f,'r',encoding='utf-8') as f: ids={line.strip() for line in f}
            d.content=ft.ListView(expand=True,controls=[ft.Checkbox(label=t['display_path'],value=(t['id'] in ids),data=t['id']) for t in tasks])
            page.update()
        threading.Thread(target=setup_worker,args=(json_path,downloader_output_dir_field.value,log_manager_dialog),daemon=True).start()

    # --- UI CONTROLS AND PAGE DEFINITIONS ---
    channel_dialog,log_manager_dialog = ft.AlertDialog(modal=True,title=ft.Text("Choose Channel")),ft.AlertDialog(modal=True,title=ft.Text("Manage Log"))
    youtube_file_picker,downloader_file_picker = ft.FilePicker(on_result=on_youtube_video_picked),ft.FilePicker(on_result=on_downloader_json_picked)
    directory_picker = ft.FilePicker(on_result=on_bulk_folder_picked)
    page.overlay.extend([channel_dialog,log_manager_dialog,youtube_file_picker,downloader_file_picker,directory_picker])
    
    youtube_auth_button,youtube_stop_button = ft.ElevatedButton("Login & Select Channel",on_click=start_youtube_auth_flow,icon=ft.icons.LOGIN),ft.ElevatedButton("Stop",on_click=stop_youtube_auth_flow,icon=ft.icons.CANCEL,bgcolor=ft.colors.RED,visible=False)
    youtube_auth_stack = ft.Stack([youtube_auth_button,youtube_stop_button])
    youtube_selected_channel_text = ft.Text("Target Channel: None",size=16,weight=ft.FontWeight.BOLD)
    
    # --- MODIFICATION: Added Privacy Dropdown for Single Uploader ---
    youtube_title_field,youtube_description_field = ft.TextField(label="Video Title"),ft.TextField(label="Video Description",multiline=True)
    youtube_privacy_dropdown = ft.Dropdown(
        label="Privacy", value="private", width=200,
        options=[
            ft.dropdown.Option("private", "Private"),
            ft.dropdown.Option("unlisted", "Unlisted"),
            ft.dropdown.Option("public", "Public"),
        ]
    )
    youtube_selected_file_text = ft.Text("No video selected.")
    youtube_upload_button,youtube_upload_progress_ring = ft.ElevatedButton("Upload",on_click=start_youtube_upload_flow,icon=ft.icons.UPLOAD),ft.ProgressRing(visible=False,width=20)
    
    # --- MODIFICATION: Added Privacy Dropdown for Bulk Uploader ---
    bulk_upload_folder_text = ft.Text("No folder selected.")
    bulk_privacy_dropdown = ft.Dropdown(
        label="Privacy", value="private", width=200,
        options=[
            ft.dropdown.Option("private", "Private"),
            ft.dropdown.Option("unlisted", "Unlisted"),
            ft.dropdown.Option("public", "Public"),
        ]
    )
    bulk_upload_button = ft.ElevatedButton("Start Bulk Upload",on_click=start_bulk_upload_flow,icon=ft.icons.UPLOAD_FILE)

    youtube_view = ft.Column([
        ft.Text("YouTube Uploader",size=24,weight=ft.FontWeight.BOLD),
        ft.Text("Step 1: Authenticate"),
        youtube_auth_stack,
        youtube_selected_channel_text,
        ft.Divider(),
        ft.Text("Step 2: Upload Single Video",size=20),
        youtube_title_field,
        youtube_description_field,
        ft.Row([youtube_privacy_dropdown]),
        ft.Row([ft.ElevatedButton("Select Video",on_click=lambda _:youtube_file_picker.pick_files(allowed_extensions=["mp4","mov"])),youtube_selected_file_text]),
        ft.Row([youtube_upload_button,youtube_upload_progress_ring]),
        ft.Divider(),
        ft.Text("Step 3: Bulk Upload from Folder (Recursive)",size=20),
        ft.Row([ft.ElevatedButton("Select Folder",on_click=lambda _:directory_picker.get_directory_path(),icon=ft.icons.FOLDER_OPEN),bulk_upload_folder_text]),
        ft.Row([bulk_privacy_dropdown, bulk_upload_button]),
    ],spacing=12,scroll=ft.ScrollMode.AUTO)
    
    downloader_token_field = ft.TextField(label="Bearer Token",password=True,can_reveal_password=True)
    downloader_output_dir_field = ft.TextField(label="Output Directory Name (Optional)")
    downloader_selected_json_text = ft.Text("No JSON file selected.")
    downloader_start_button = ft.ElevatedButton("Start Download",on_click=start_downloader_flow,icon=ft.icons.DOWNLOAD)
    downloader_stop_button = ft.ElevatedButton("Stop Download", on_click=stop_downloader_flow, icon=ft.icons.CANCEL, bgcolor=ft.colors.RED, visible=False)
    downloader_manage_log_button = ft.ElevatedButton("Manage Log",on_click=open_log_manager,icon=ft.icons.EDIT_DOCUMENT)
    downloader_button_row = ft.Row([ft.Stack([downloader_start_button, downloader_stop_button]), downloader_manage_log_button])
    downloader_progress_bar = ft.ProgressBar(visible=False,width=400)
    downloader_view = ft.Column([ft.Text("Course Downloader",size=24,weight=ft.FontWeight.BOLD),ft.Text("Requires 'aria2c' to be installed."),downloader_token_field,ft.Row([ft.ElevatedButton("Select Course JSON",on_click=lambda _:downloader_file_picker.pick_files(allowed_extensions=["json"])),downloader_selected_json_text]),downloader_output_dir_field,downloader_button_row,downloader_progress_bar],spacing=12)
    
    console_log_view = ft.TextField(multiline=True,read_only=True,expand=True,value="Welcome!\n",border_color="grey")
    page_container = ft.Column([youtube_view],expand=True)
    
    def nav_change(e):
        idx=e.control.selected_index;page_container.controls.clear();console_log_view.visible=(idx==2)
        if idx==0:page_container.controls.append(youtube_view)
        elif idx==1:page_container.controls.append(downloader_view)
        elif idx==2:page_container.controls.append(console_log_view)
        page.update()
        
    navigation_rail = ft.NavigationRail(selected_index=0,label_type=ft.NavigationRailLabelType.ALL,on_change=nav_change,destinations=[ft.NavigationRailDestination(icon=ft.icons.UPLOAD_OUTLINED,selected_icon=ft.icons.UPLOAD,label="YouTube"),ft.NavigationRailDestination(icon=ft.icons.DOWNLOAD_OUTLINED,selected_icon=ft.icons.DOWNLOAD,label="Downloader"),ft.NavigationRailDestination(icon=ft.icons.TERMINAL_OUTLINED,selected_icon=ft.icons.TERMINAL,label="Log")])
    sys.stdout=ConsoleLogger(console_log_view,page);sys.stderr=ConsoleLogger(console_log_view,page)
    page.add(ft.Row([navigation_rail,ft.VerticalDivider(width=1),page_container],expand=True))

if __name__=="__main__":
    ft.app(target=main)
