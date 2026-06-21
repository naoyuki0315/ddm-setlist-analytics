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
            if not row: continue
            if header_index == -1:
                for idx, col in enumerate(row):
                    if '楽曲名' in col:
                        header_index = idx
                        break
                if header_index != -1:
                    continue
            
            if header_index != -1 and len(row) > header_index:
                song = row[header_index].strip()
                if song and '楽曲名' not in song and not song.isdigit():
                    songs.append(song)
        
        master_list = list(set(songs))
        print(f"【ログ】スプレッドシートから {len(master_list)} 件の持ち曲リストを読み込みました。")
        if master_list:
            print(f"【ログ】持ち曲の例: {master_list[:5]}")
        else:
            print("【⚠️警告】スプレッドシートから曲名が1件も読み込めませんでした。URLか公開設定を確認してください。")
        return master_list
    except Exception as e:
        print(f"【エラー】マスターリストの読み込みに失敗しました: {e}")
        return []

def normalize_for_match(s):
    s = unicodedata.normalize('NFKC', s).lower()
    # 矢印などの記号も一括排除
    s = re.sub(r'[\s\'"’`・\(\)（）\-\[\]\?,_\.!\/\\：:※\*\+ →]', '', s)
    replacements = {
        'フーチークーチーマン': 'hoochiecoochieman',
        'working': 'workin',
        'allright': 'alright',
    }
    for key, val in replacements.items():
        s = s.replace(key, val)
    return s

def analyze_description(description, date_str, video_id, master_songs, data_store):
    if not description: return
    is_encore_mode = 'アンコール' in description.lower() or 'encore' in description.lower()
    
    lines = description.split('\n')
    for line in lines:
        line = line.strip()
        
        match = re.search(r'\d{1,2}:\d{2}', line)
        if not match: continue
        
        if any(x in line.lower() for x in ['members', 'vocal', 'guitar', 'harp', 'drums', 'bass']): continue

        timestamp = match.group(0)
        before, after = line.split(timestamp, 1)
        
        clean_before = re.sub(r'^\d+[\.\s]*', '', before).strip()
        clean_before = re.sub(r'^[・\-\s/]+', '', clean_before).strip()
        
        clean_after = re.sub(r'^\d+[\.\s]*', '', after).strip()
        clean_after = re.sub(r'^[・\-\s/]+', '', clean_after).strip()
        
        raw_title = ""
        if len(clean_before) > 2 and not any(x in clean_before.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps']):
            raw_title = clean_before
        elif len(clean_after) > 2 and not any(x in clean_after.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps']):
            raw_title = clean_after
            
        if not raw_title: continue

        matched_song = None
        clean_raw = normalize_for_match(raw_title)
        
        for master in master_songs:
            m_norm = normalize_for_match(master)
            if m_norm in clean_raw or clean_raw in m_norm:
                matched_song = master
                break
        
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
    if not API_KEY:
        print("【エラー】YOUTUBE_API_KEY が環境変数に設定されていません。")
        return
    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)
        
        print("【ログ】YouTubeから動画リストを取得中...")
        channel_res = youtube.channels().list(part="contentDetails", id=CHANNEL_ID).execute()
        uploads_playlist_id = channel_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        
        videos = []
        next_page_token = None
        while True:
            playlist_res = youtube.playlistItems().list(part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page_token).execute()
            videos.extend(playlist_res.get("items", []))
            next_page_token = playlist_res.get("nextPageToken")
            if not next_page_token: break
        print(f"【ログ】YouTubeから {len(videos)} 件の動画を取得しました。")
    except Exception as e:
        print(f"【エラー】YouTube APIからのデータ取得中にエラーが発生しました: {e}")
        return

    master_songs = load_master_songs_from_web(CSV_URL)
    data_store = {'main': {}, 'encores': {}, 'unknown': []}

    print("【ログ】動画説明欄の解析を開始します...")
    for video in videos:
        try:
            snippet = video.get("snippet", {})
            desc = snippet.get("description", "")
            pub_at = snippet.get("publishedAt", "2000-01-01T00:00:00Z").split('T')[0]
            v_id = snippet.get("resourceId", {}).get("videoId", "")
            if v_id:
                analyze_description(desc, pub_at, v_id, master_songs, data_store)
        except Exception as e:
            # 万が一特定の動画でバグっても、スキップして処理を続行させる
            continue

    output = {
        "lastUpdated": datetime.now(timezone(timedelta(hours=+9))).strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, **v} for k, v in data_store['main'].items()],
        "encores": [{"name": k, **v} for k, v in data_store['encores'].items()],
        "unknown": data_store['unknown']
    }
    try:
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 集計完了！ファイル保存成功 main: {len(output['main'])}件, encores: {len(output['encores'])}件, unknown: {len(output['unknown'])}件")
    except Exception as e:
        print(f"【エラー】data.jsonの保存に失敗しました: {e}")

if __name__ == "__main__":
    main()
