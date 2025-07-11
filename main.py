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
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.upload"]
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

# --- HELPER FUNCTIONS (Condensed for brevity, mostly unchanged) ---
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
def upload_video(youtube, cid, path, title, desc, ring, btn):
    try:
        ring.visible = True; btn.disabled = True; page.update()
        body = {'snippet': {'title': title, 'description': desc}, 'status': {'privacyStatus': 'private'}}
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
    s = "".join(c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in name)
    return ' '.join(s.split()).strip()[:150]
def _get_course_structure(json_file, output_dir=None):
    try:
        with open(json_file, 'r', encoding='utf-8') as f: course_data = json.load(f).get('course')
        if not course_data: raise ValueError("Invalid JSON: 'course' key not found.")
        course_title_raw, course_title_sanitized = course_data.get('title', 'Unknown Course'), sanitize_filename(output_dir or course_data.get('title', 'Unknown Course'))
        tasks = []
        for i, chap in enumerate(course_data.get('chapters', [])):
            chap_title = sanitize_filename(f"{i+1:02d} - {chap.get('title', 'Chap')}")
            for j, less in enumerate(chap.get('lessons', [])):
                less_info, less_title = less.get('lesson', {}), sanitize_filename(f"{j+1:02d} - {less_info.get('title', 'Less')}")
                for res_item in sorted(less_info.get('resources', []), key=lambda x: x.get('order', 99)):
                    res, file_url = res_item.get('resource', {}), res_item.get('resource', {}).get('fileUrl')
                    if not file_url: continue
                    ext, sub_dir = ('.mp4', "Videos") if res.get('type') == 'video' else ('.pdf', "TaiLieu") if res.get('type') == 'document' else (None, None)
                    if not ext: continue
                    filename = sanitize_filename(res.get('title', 'file')) + ext
                    tasks.append({'id': file_url, 'type': res.get('type'), 'file_url_part': file_url, 'save_dir': os.path.join(course_title_sanitized, chap_title, less_title, sub_dir), 'filename': filename, 'display_path': os.path.join(chap_title, less_title, sub_dir, filename)})
        return course_title_sanitized, tasks, course_title_raw
    except Exception as e: print(f"❌ Error processing JSON file: {e}"); return None, None, None
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
    page.session.set("bulk_upload_folder_path", None) # NEW session variable for bulk upload

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
        path, cid, yt, title = page.session.get("video_to_upload_path"), page.session.get("selected_channel_id"), page.session.get("youtube_client"), youtube_title_field.value
        if not all([path, title, cid, yt]): print("❌ Error: Missing video, title, or channel."); youtube_upload_button.disabled = False; page.update(); return
        upload_video(yt, cid, path, title, youtube_description_field.value, youtube_upload_progress_ring, youtube_upload_button)
    
    # --- NEW: Worker for Bulk Upload ---
    def bulk_upload_worker(folder_path, start_button):
        try:
            yt = page.session.get("youtube_client")
            cid = page.session.get("selected_channel_id")
            if not all([yt, cid]):
                print("❌ Error: Please authenticate and select a channel first.")
                return

            print(f"Scanning folder '{folder_path}' for videos...")
            video_extensions = (".mp4", ".mov", ".mkv", ".avi", ".webm")
            videos_to_upload = [f for f in os.listdir(folder_path) if f.lower().endswith(video_extensions)]

            if not videos_to_upload:
                print("No video files found in the selected directory.")
                return

            print(f"Found {len(videos_to_upload)} videos. Starting bulk upload...")
            for i, filename in enumerate(videos_to_upload):
                video_path = os.path.join(folder_path, filename)
                video_title = Path(filename).stem # Use filename without extension as title
                print(f"\n--- Uploading video {i+1} of {len(videos_to_upload)}: {filename} ---")
                
                # We can reuse the single upload UI elements for feedback on the current file
                success = upload_video(yt, cid, video_path, video_title, f"Uploaded via Flet Bulk Uploader.", youtube_upload_progress_ring, youtube_upload_button)
                if not success:
                    print(f"Stopping bulk upload due to an error with '{filename}'.")
                    break # Stop the whole process if one video fails
            print("\n--- Bulk upload process finished. ---")
        except Exception as e:
            print(f"❌ A critical error occurred during bulk upload: {e}")
        finally:
            start_button.disabled = False
            page.update()

    # (Downloader worker is unchanged)
    def downloader_worker(json_path, token, output_dir, start_button, progress_bar):
        try:
            if not token: print("❌ Error: Bearer Token is required."); return
            headers, (course_title, tasks, _) = {'Authorization': f'Bearer {token}'}, _get_course_structure(json_path, output_dir)
            if not tasks: return
            log_file, downloaded_ids = os.path.join(course_title, ".download_log.txt"), set()
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8') as f: downloaded_ids = {line.strip() for line in f}
            print(f"Found {len(tasks)} files. {len(downloaded_ids)} in log."); progress_bar.visible = True; page.update()
            for i, task in enumerate(tasks):
                progress_bar.value = (i + 1) / len(tasks); page.update()
                if task['id'] in downloaded_ids: print(f"Skipping logged: {task['filename']}"); continue
                print(f"Processing {i+1}/{len(tasks)}: {task['filename']}")
                url = get_presigned_url(task['file_url_part'], headers) if task['type'] == 'video' else f"{BASE_DOC_URL}{task['file_url_part']}"
                if url and download_with_aria2(url, task['save_dir'], task['filename']):
                    with open(log_file, 'a', encoding='utf-8') as f_log: f_log.write(f"{task['id']}\n"); downloaded_ids.add(task['id'])
                else: print("  Download failed, skipping.")
            print("\nCourse download complete!")
        finally: start_button.disabled = False; progress_bar.visible = False; page.update()

    # --- UI Event Handlers ---
    def start_youtube_auth_flow(e):
        id = page.session.get("youtube_auth_thread_id") + 1; page.session.set("youtube_auth_thread_id", id)
        youtube_auth_button.visible = False; youtube_stop_button.visible = True; page.update()
        threading.Thread(target=youtube_auth_worker, args=(id,), daemon=True).start()
    def stop_youtube_auth_flow(e):
        print("YouTube auth cancelled."); youtube_auth_button.visible = True; youtube_stop_button.visible = False; page.update()
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
            channel_dialog.open = False; stop_youtube_auth_flow(None); page.update()
        channel_dialog.content = ft.Column([ft.ListTile(title=ft.Text(c['title']), on_click=channel_selected, data=c['id']) for c in channels], tight=True, scroll=True)
        channel_dialog.open = True; page.update()

    # --- NEW: Event Handlers for Bulk Upload ---
    def on_bulk_folder_picked(e: ft.FilePickerResultEvent):
        if e.path:
            page.session.set("bulk_upload_folder_path", e.path)
            bulk_upload_folder_text.value = f"Selected folder: {e.path}"
        else:
            page.session.set("bulk_upload_folder_path", None)
            bulk_upload_folder_text.value = "No folder selected."
        page.update()

    def start_bulk_upload_flow(e):
        folder_path = page.session.get("bulk_upload_folder_path")
        if not folder_path:
            print("❌ Error: Please select a folder first.")
            return
        e.control.disabled = True
        page.update()
        threading.Thread(target=bulk_upload_worker, args=(folder_path, e.control), daemon=True).start()

    # (Other handlers are unchanged)
    def start_downloader_flow(e):
        token, json_path = downloader_token_field.value, page.session.get("downloader_json_path")
        if not token or not json_path: print("❌ Error: Token and JSON file are required."); return
        e.control.disabled = True; page.update()
        threading.Thread(target=downloader_worker, args=(json_path, token, downloader_output_dir_field.value, e.control, downloader_progress_bar), daemon=True).start()
    def on_downloader_json_picked(e: ft.FilePickerResultEvent):
        if e.files: path = e.files[0].path; page.session.set("downloader_json_path", path); downloader_selected_json_text.value = f"Selected: {Path(path).name}"
        else: page.session.set("downloader_json_path", None); downloader_selected_json_text.value = "No file selected."
        page.update()
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
    channel_dialog = ft.AlertDialog(modal=True, title=ft.Text("Choose YouTube Channel"))
    log_manager_dialog = ft.AlertDialog(modal=True, title=ft.Text("Manage Download Log"), content=ft.Text("..."))
    youtube_file_picker, downloader_file_picker = ft.FilePicker(on_result=on_youtube_video_picked), ft.FilePicker(on_result=on_downloader_json_picked)
    directory_picker = ft.FilePicker(on_result=on_bulk_folder_picked) # NEW directory picker
    page.overlay.extend([channel_dialog, log_manager_dialog, youtube_file_picker, downloader_file_picker, directory_picker])

    # == Page 1: YouTube Uploader View ==
    youtube_auth_button, youtube_stop_button = ft.ElevatedButton("Login & Select Channel", on_click=start_youtube_auth_flow, icon=ft.icons.LOGIN), ft.ElevatedButton("Stop", on_click=stop_youtube_auth_flow, icon=ft.icons.CANCEL, bgcolor=ft.colors.RED, visible=False)
    youtube_auth_stack = ft.Stack([youtube_auth_button, youtube_stop_button])
    youtube_selected_channel_text = ft.Text("Target Channel: None", size=16, weight=ft.FontWeight.BOLD)
    youtube_title_field, youtube_description_field = ft.TextField(label="Video Title (for single upload)"), ft.TextField(label="Video Description", multiline=True)
    youtube_selected_file_text = ft.Text("No video selected.")
    youtube_upload_button, youtube_upload_progress_ring = ft.ElevatedButton("Upload", on_click=start_youtube_upload_flow, icon=ft.icons.UPLOAD), ft.ProgressRing(visible=False, width=20)
    # --- NEW UI for Bulk Upload ---
    bulk_upload_folder_text = ft.Text("No folder selected.")
    bulk_upload_button = ft.ElevatedButton("Start Bulk Upload", on_click=start_bulk_upload_flow, icon=ft.icons.UPLOAD_FILE)
    
    youtube_view = ft.Column([
        ft.Text("YouTube Uploader", size=24, weight=ft.FontWeight.BOLD),
        ft.Text("Step 1: Authenticate and choose channel."), youtube_auth_stack, youtube_selected_channel_text,
        ft.Divider(),
        ft.Text("Step 2: Upload a Single Video", size=20), youtube_title_field, youtube_description_field,
        ft.Row([ft.ElevatedButton("Select Video", on_click=lambda _: youtube_file_picker.pick_files(allowed_extensions=["mp4", "mov"])), youtube_selected_file_text]),
        ft.Row([youtube_upload_button, youtube_upload_progress_ring]),
        ft.Divider(),
        # --- NEW UI Section Added to Layout ---
        ft.Text("Step 3: Bulk Upload from a Folder", size=20),
        ft.Row([ft.ElevatedButton("Select Folder", on_click=lambda _: directory_picker.get_directory_path(), icon=ft.icons.FOLDER_OPEN), bulk_upload_folder_text]),
        bulk_upload_button,
    ], spacing=12, scroll=ft.ScrollMode.AUTO)

    # == Page 2: Course Downloader View (Unchanged) ==
    downloader_token_field = ft.TextField(label="Bearer Token", password=True, can_reveal_password=True)
    downloader_output_dir_field = ft.TextField(label="Output Directory Name (Optional)")
    downloader_selected_json_text = ft.Text("No JSON file selected.")
    downloader_start_button, downloader_manage_log_button = ft.ElevatedButton("Start Download", on_click=start_downloader_flow, icon=ft.icons.DOWNLOAD), ft.ElevatedButton("Manage Log", on_click=open_log_manager, icon=ft.icons.EDIT_DOCUMENT)
    downloader_progress_bar = ft.ProgressBar(visible=False, width=400)
    downloader_view = ft.Column([ft.Text("Course Downloader", size=24, weight=ft.FontWeight.BOLD), ft.Text("Requires 'aria2c' to be installed."), downloader_token_field, ft.Row([ft.ElevatedButton("Select Course JSON", on_click=lambda _: downloader_file_picker.pick_files(allowed_extensions=["json"])), downloader_selected_json_text]), downloader_output_dir_field, ft.Row([downloader_start_button, downloader_manage_log_button]), downloader_progress_bar], spacing=12)

    # == Page 3: Console Log View (Unchanged) ==
    console_log_view = ft.TextField(multiline=True, read_only=True, expand=True, value="Welcome!\n", border_color="grey")

    # --- Main App Structure (Unchanged) ---
    page_container = ft.Column([youtube_view], expand=True)
    def nav_change(e):
        idx = e.control.selected_index; page_container.controls.clear()
        console_log_view.visible = (idx == 2)
        if idx == 0: page_container.controls.append(youtube_view)
        elif idx == 1: page_container.controls.append(downloader_view)
        elif idx == 2: page_container.controls.append(console_log_view)
        page.update()
    navigation_rail = ft.NavigationRail(selected_index=0, label_type=ft.NavigationRailLabelType.ALL, on_change=nav_change, destinations=[ft.NavigationRailDestination(icon=ft.icons.UPLOAD_OUTLINED, selected_icon=ft.icons.UPLOAD, label="YouTube"), ft.NavigationRailDestination(icon=ft.icons.DOWNLOAD_OUTLINED, selected_icon=ft.icons.DOWNLOAD, label="Downloader"), ft.NavigationRailDestination(icon=ft.icons.TERMINAL_OUTLINED, selected_icon=ft.icons.TERMINAL, label="Log")])
    sys.stdout = ConsoleLogger(console_log_view, page); sys.stderr = ConsoleLogger(console_log_view, page)
    page.add(ft.Row([navigation_rail, ft.VerticalDivider(width=1), page_container], expand=True))

if __name__ == "__main__":
    ft.app(target=main)