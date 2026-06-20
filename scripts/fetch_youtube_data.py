import os
import re
import json
import unicodedata
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build

API_KEY = os.environ.get("YOUTUBE_API_KEY")
HANDLE = "@70315"

def load_master_songs(csv_path):
    songs = []
    if not os.path.exists(csv_path):
        return songs
    
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            song = line.strip().split(',')[0].strip()
            if song and song != '楽曲名' and song != 'No.':
                songs.append(song)
    return list(set(songs))

def normalize_for_match(s):
    s = unicodedata.normalize('NFKC', s)
    s = s.lower()
    
    s = s.replace('フーチークーチーマン', 'hoochiecoochieman')
    s = s.replace('working', 'workin')
    s = s.replace('allright', 'alright')
    s = s.replace('kilin', 'killin')
    s = s.replace('baby', '') 
    s = s.replace('walking', 'walkin')
    
    s = re.sub(r'[\s\'"’`・\(\)（）\-\[\]]', '', s)
    return s

def clean_song_title(raw_title):
    title = re.sub(r'\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2}', '', raw_title)
    title = re.sub(r'^[\s ]*\d+[.．\s ]+', '', title)
    title = re.sub(r'^[\s ]*[・\-\*※][\s ]*', '', title)
    title = re.sub(r'^(リクエスト|曲)[\s ]*', '', title)
    title = re.sub(r'\(?アンコール曲?\)?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'encore', '', title, flags=re.IGNORECASE)
    return title.strip()

def analyze_description(description, date_str, video_id, master_songs, data_store):
    lines = description.split('\n')
    is_encore_mode = False

    for line in lines:
        line = line.strip()
        if not line: continue

        if line.lower() == 'アンコール' or line.lower() == 'encore':
            is_encore_mode = True
            continue

        if not re.search(r'\d{1,2}:\d{2}', line):
            continue

        lower_line = line.lower()
        if any(ignore in lower_line for ignore in ['intro', 'greeting', 'mc', 'オープニング', 'エンディング', 'トーク', 'メンバー紹介']):
            continue

        is_encore_line = is_encore_mode or ('アンコール' in line) or ('encore' in lower_line)
        raw_title = clean_song_title(line)
        if not raw_title: continue

        matched_song = None
        clean_raw = normalize_for_match(raw_title)
        
        for master in master_songs:
            clean_master = normalize_for_match(master)
            if clean_master in clean_raw or clean_raw in clean_master:
                matched_song = master
                break
        
        target_dict = data_store['encores'] if is_encore_line else data_store['main']

        if matched_song:
            if matched_song not in target_dict:
                target_dict[matched_song] = {'count': 0, 'lastPlayed': '', 'playDates': [], 'urls': []}
            
            target_dict[matched_song]['count'] += 1
            target_dict[matched_song]['playDates'].append(date_str)
            target_dict[matched_song]['urls'].append({'date': date_str, 'url': f"https://www.youtube.com/watch?v={video_id}"})
            
            if not target_dict[matched_song]['lastPlayed'] or date_str > target_dict[matched_song]['lastPlayed']:
                target_dict[matched_song]['lastPlayed'] = date_str
        else:
            if raw_title not in data_store['unknown']:
                data_store['unknown'].append(raw_title)

def main():
    if not API_KEY:
        print("エラー: YOUTUBE_API_KEY が見つかりません。")
        return

    youtube = build('youtube', 'v3', developerKey=API_KEY)
    
    channel_res = youtube.channels().list(part="contentDetails", forHandle=HANDLE).execute()
    if not channel_res.get("items"): return
        
    uploads_playlist_id = channel_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    
    videos = []
    next_page_token = None
    while True:
        playlist_res = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page_token
        ).execute()
        videos.extend(playlist_res.get("items", []))
        next_page_token = playlist_res.get("nextPageToken")
        if not next_page_token: break

    master_songs = load_master_songs('master_songs.csv')
    data_store = {'main': {}, 'encores': {}, 'unknown': []}

    print(f"全{len(videos)}件の動画からライブ演奏データを集計します...")
    for idx, video in enumerate(videos):
        snippet = video["snippet"]
        title = snippet["title"]
        video_id = snippet["resourceId"]["videoId"] # 追加
        if not re.search(r'\d', title): continue
            
        date_str = snippet["publishedAt"].split('T')[0]
        description = snippet["description"]
        
        print(f"[{idx+1}/{len(videos)}] 解析中: {title} ({date_str})")
        analyze_description(description, date_str, video_id, master_songs, data_store)

    JST = timezone(timedelta(hours=+9), 'JST')
    
    output = {
        "lastUpdated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, "count": v['count'], "lastPlayed": v['lastPlayed'], "playDates": v['playDates'], "urls": v['urls']} for k, v in data_store['main'].items()],
        "encores": [{"name": k, "count": v['count'], "lastPlayed": v['lastPlayed'], "playDates": v['playDates'], "urls": v['urls']} for k, v in data_store['encores'].items()],
        "unknown": data_store['unknown']
    }

    print("保存処理に入ります...")
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ 全ての処理が完了しました。")

if __name__ == "__main__":
    main()
