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
        return master_list
    except Exception as e:
        print(f"【エラー】マスターリストの読み込みに失敗しました: {e}")
        return []

def normalize_for_match(s):
    s = unicodedata.normalize('NFKC', s).lower()
    # 記号やスペースを一括排除
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
        
        # 1. タイムスタンプ（MM:SS や HH:MM:SS）をすべて抽出
        timestamps = re.findall(r'\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b', line)
        if not timestamps: continue
        
        # 2. 行からタイムスタンプ部分を完全に消去
        clean_line = line
        for ts in timestamps:
            clean_line = clean_line.replace(ts, "")
            
        # 3. 先頭の曲番（1. や 02 など）や不要な記号を掃除
        clean_line = re.sub(r'^\d+[\.\s\-・]*', '', clean_line)
        # カッコ書き（Guitar solo等）を一時的に除去して曲名判定を正確にする
        clean_line = re.sub(r'[\(\[\{【].*?[\)\]\}】]', '', clean_line)
        clean_line = clean_line.strip(" ・-/:,[]()")
        
        # 4. 明らかなノイズ行（MCや挨拶、メンバー紹介のみの行）はスキップ
        if not clean_line or any(x in clean_line.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps', 'members', 'vocal:', 'drums:', 'bass:']):
            continue

        # 5. 照合ロジック
        matched_song = None
        clean_raw = normalize_for_match(clean_line)
        
        for master in master_songs:
            m_norm = normalize_for_match(master)
            if m_norm and (m_norm in clean_raw or clean_raw in m_norm):
                matched_song = master
                break
        
        is_encore_line = is_encore_mode and ('アンコール' in line.lower() or 'encore' in line.lower() or 'アンコール' in clean_line)
        target_dict = data_store['encores'] if is_encore_line else data_store['main']

        if matched_song:
            if matched_song not in target_dict:
                target_dict[matched_song] = {'count': 0, 'lastPlayed': '', 'playDates': [], 'urls': []}
            target_dict[matched_song]['count'] += 1
            target_dict[matched_song]['playDates'].append(date_str)
            target_dict[matched_song]['urls'].append({'date': date_str, 'url': f"https://www.youtube.com/watch?v={video_id}"})
            target_dict[matched_song]['lastPlayed'] = max(target_dict[matched_song]['lastPlayed'], date_str)
        else:
            # 2文字以下の意味のない記号ゴミはunknownに入れない
            if len(clean_line) > 2 and clean_line not in data_store['unknown']:
                data_store['unknown'].append(clean_line)

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
        # APIクォータ切れを防ぐため、最大3ページ（150本）までに制限（必要に応じて調整してください）
        page_count = 0
        while page_count < 3:
            playlist_res = youtube.playlistItems().list(part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page_token).execute()
            videos.extend(playlist_res.get("items", []))
            next_page_token = playlist_res.get("nextPageToken")
            page_count += 1
            if not next_page_token: break
            
        print(f"【ログ】YouTubeから {len(videos)} 件の動画を取得しました。")
    except Exception as e:
        print(f"【エラー】YouTube APIからのデータ取得中にエラーが発生しました（クォータ切れの可能性あり）: {e}")
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
