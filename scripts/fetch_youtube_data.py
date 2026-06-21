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
CHANNEL_ID = "UC4m7H3u7Uo-QZq8e9s4_7Sg" 
CSV_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTzWOELNEzNkvAb1Nld1Tjzv0_Z5mvRvuQdeH20jy-LYP0cycHgpWcpR6rcSBFqm-5lGKZYLgSmW4cg/pub?gid=842461559&single=true&output=csv'

def load_master_songs_from_web(csv_url):
    try:
        response = requests.get(csv_url)
        response.encoding = 'utf-8-sig'
        f = io.StringIO(response.text)
        reader = csv.reader(f)
        songs = []
        header_index = -1
        for row in reader:
            if '楽曲名' in row:
                header_index = row.index('楽曲名')
                continue
            if header_index != -1 and len(row) > header_index:
                song = row[header_index].strip()
                if song and song != '楽曲名' and not song.isdigit():
                    songs.append(song)
        return list(set(songs))
    except Exception as e:
        print(f"マスターリストの読み込みに失敗しました: {e}")
        return []

def normalize_for_match(s):
    s = unicodedata.normalize('NFKC', s).lower()
    replacements = {
        'hoochiecoochieman': 'hoochiecoochieman', 'フーチークーチーマン': 'hoochiecoochieman',
        'workin': 'workin', 'working': 'workin', 'alright': 'alright', 'allright': 'alright',
        'killin': 'killin', 'kilin': 'killin', 'walking': 'walkin', 'walkin': 'walkin',
        'baby': '', 'mojo': 'gotmymojoworkin', 'mojoworkin': 'gotmymojoworkin'
    }
    s = re.sub(r'[\s\'"’`・\(\)（）\-\[\]]', '', s)
    for key, val in replacements.items(): s = s.replace(key, val)
    return s

def analyze_description(description, date_str, video_id, master_songs, data_store):
    is_encore_mode = 'アンコール' in description.lower() or 'encore' in description.lower()
    
    lines = description.split('\n')
    for line in lines:
        line = line.strip()
        
        # タイムスタンプ（例 01:23 や 1:23）を探す
        match = re.search(r'\d{1,2}:\d{2}', line)
        if not match: continue
        
        # メンバー紹介や楽器名の行は除外
        if any(x in line.lower() for x in ['members', 'vocal', 'guitar', 'harp', 'drums', 'bass']): continue

        timestamp = match.group(0)
        # タイムスタンプの「前」と「後」に分解
        before, after = line.split(timestamp, 1)
        
        # 前後の文字列から、行頭の数字や余計な記号を掃除
        clean_before = re.sub(r'^\d+[\.\s]*', '', before).strip()
        clean_before = re.sub(r'^[・\-\s/]+', '', clean_before).strip()
        
        clean_after = re.sub(r'^\d+[\.\s]*', '', after).strip()
        clean_after = re.sub(r'^[・\-\s/]+', '', clean_after).strip()
        
        # どちらに曲名が入っているか判定（文字数が長く、ノイズでない方を採用）
        raw_title = ""
        if len(clean_before) > 2 and not any(x in clean_before.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps']):
            raw_title = clean_before
        elif len(clean_after) > 2 and not any(x in clean_after.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps']):
            raw_title = clean_after
            
        if not raw_title: continue

        # マッチング処理
        matched_song = None
        clean_raw = normalize_for_match(raw_title)
        for master in master_songs:
            if normalize_for_match(master) in clean_raw or clean_raw in normalize_for_match(master):
                matched_song = master
                break
        
        # アンコール判定
        is_encore_line = is_encore_mode and ('アンコール' in line.lower() or 'encore' in line.lower() or 'アンコール' in raw_title)
        target_dict = data_store['encores'] if is_encore_line else data_store['main']

        if matched_song:
            if matched_song not in target_dict:
                target_dict[matched_song] = {'count': 0, 'lastPlayed': '', 'playDates': [], 'urls': []}
            target_dict[matched_song]['count'] += 1
            target_dict[matched_song]['playDates'].append(date_str)
            target_dict[matched_song]['urls'].append({'date': date_str, 'url': f"https://www.youtube.com/watch?v={video_id}"})
            target_dict[matched_song]['lastPlayed'] = max(target_dict[matched_song]['lastPlayed'], date_str)
        else:
            if raw_title not in data_store['unknown']:
                data_store['unknown'].append(raw_title)

def main():
    if not API_KEY: return
    youtube = build('youtube', 'v3', developerKey=API_KEY)
    
    channel_res = youtube.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
    uploads_playlist_id = channel_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    
    videos = []
    next_page_token = None
    while True:
        playlist_res = youtube.playlistItems().list(part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page_token).execute()
        videos.extend(playlist_res.get("items", []))
        next_page_token = playlist_res.get("nextPageToken")
        if not next_page_token: break

    master_songs = load_master_songs_from_web(CSV_URL)
    data_store = {'main': {}, 'encores': {}, 'unknown': []}

    for video in videos:
        snippet = video["snippet"]
        analyze_description(snippet["description"], snippet["publishedAt"].split('T')[0], snippet["resourceId"]["videoId"], master_songs, data_store)

    output = {
        "lastUpdated": datetime.now(timezone(timedelta(hours=+9))).strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, **v} for k, v in data_store['main'].items()],
        "encores": [{"name": k, **v} for k, v in data_store['encores'].items()],
        "unknown": data_store['unknown']
    }
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ 集計完了")

if __name__ == "__main__":
    main()
