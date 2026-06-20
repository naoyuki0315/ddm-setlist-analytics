import os
import re
import json
import unicodedata
import requests
import csv
import io
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build

API_KEY = os.environ.get("YOUTUBE_API_KEY")
HANDLE = "@70315"
# スプレッドシート公開CSVのURL
CSV_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTzWOELNEzNkvAb1Nld1Tjzv0_Z5mvRvuQdeH20jy-LYP0cycHgpWcpR6rcSBFqm-5lGKZYLgSmW4cg/pub?gid=842461559&single=true&output=csv'

def load_master_songs_from_web(csv_url):
    """スプレッドシートのCSVから「楽曲名」列を検索して読み込む"""
    try:
        response = requests.get(csv_url)
        response.encoding = 'utf-8-sig'
        f = io.StringIO(response.text)
        reader = csv.reader(f)
        
        songs = []
        header_index = -1
        
        for row in reader:
            # 「楽曲名」というヘッダーがある列を探す
            if '楽曲名' in row:
                header_index = row.index('楽曲名')
                continue
            
            # ヘッダーが見つかった後、その列のデータを取得
            if header_index != -1 and len(row) > header_index:
                song = row[header_index].strip()
                # 楽曲名として妥当なものだけ追加
                if song and song != '楽曲名' and not song.isdigit():
                    songs.append(song)
        
        print(f"DEBUG: マスターリスト読み込み完了。{len(set(songs))}件の楽曲を取得しました。")
        return list(set(songs))
    except Exception as e:
        print(f"マスターリストの読み込みに失敗しました: {e}")
        return []

def normalize_for_match(s):
    s = unicodedata.normalize('NFKC', s).lower()
    s = s.replace('フーチークーチーマン', 'hoochiecoochieman')
    s = s.replace('working', 'workin')
    s = s.replace('allright', 'alright')
    s = s.replace('kilin', 'killin')
    s = s.replace('baby', '') 
    s = s.replace('walking', 'walkin')
    return re.sub(r'[\s\'"’`・\(\)（）\-\[\]]', '', s)

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
        if line.lower() in ['アンコール', 'encore']:
            is_encore_mode = True
            continue
        if not re.search(r'\d{1,2}:\d{2}', line):
            continue

        lower_line = line.lower()
        if any(ignore in lower_line for ignore in ['intro', 'greeting', 'mc', 'オープニング', 'エンディング', 'トーク', 'メンバー紹介']):
            continue

        raw_title = clean_song_title(line)
        if not raw_title: continue

        matched_song = None
        clean_raw = normalize_for_match(raw_title)
        for master in master_songs:
            if normalize_for_match(master) in clean_raw or clean_raw in normalize_for_match(master):
                matched_song = master
                break
        
        is_encore_line = is_encore_mode or ('アンコール' in line) or ('encore' in lower_line)
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

    master_songs = load_master_songs_from_web(CSV_URL)
    data_store = {'main': {}, 'encores': {}, 'unknown': []}

    print(f"全{len(videos)}件の動画からデータを集計します...")
    for idx, video in enumerate(videos):
        snippet = video["snippet"]
        video_id = snippet["resourceId"]["videoId"]
        date_str = snippet["publishedAt"].split('T')[0]
        description = snippet["description"]
        print(f"[{idx+1}/{len(videos)}] 解析中: {snippet['title']}")
        analyze_description(description, date_str, video_id, master_songs, data_store)

    JST = timezone(timedelta(hours=+9), 'JST')
    output = {
        "lastUpdated": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, **v} for k, v in data_store['main'].items()],
        "encores": [{"name": k, **v} for k, v in data_store['encores'].items()],
        "unknown": data_store['unknown']
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ 完了しました。")

if __name__ == "__main__":
    main()
