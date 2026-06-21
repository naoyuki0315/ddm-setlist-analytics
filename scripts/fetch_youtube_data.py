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
        
        # 🔍 【重要】ネットから取得したデータの最初の200文字をそのまま表示してみる
        print("\n" + "="*50)
        print("【デバッグ】スプレッドシートURLから届いた生データの先頭:")
        print(response.text[:200])
        print("="*50 + "\n")
        
        f = io.StringIO(response.text)
        reader = csv.reader(f)
        songs = []
        
        # 1行目をスキップしつつ、中身をのぞき見する
        try:
            header_row = next(reader)
            print(f"【デバッグ】実際の1行目のデータ (マスの数: {len(header_row)}): {header_row}")
        except StopIteration:
            print("【エラー】CSVデータが完全に空っぽです。")
            return []
            
        # 2行目以降の読み込み処理
        for index, row in enumerate(reader, start=2):
            if not row: continue
            
            # 最初の数行だけ、プログラムがどう認識しているか表示
            if index <= 5:
                print(f"【デバッグ】実際の{index}行目のデータ (マスの数: {len(row)}): {row}")
                
            if len(row) > 1:  # B列（インデックス1）が存在するか
                song = row[1].strip()  # インデックス1 ＝ B列
                if song and not song.isdigit():
                    songs.append(song)
            else:
                if index <= 5:
                    print(f"⚠️ 警告: {index}行目はマスの数が1つしかないため、B列を読み込めませんでした。")
        
        master_list = list(set(songs))
        print(f"\n【ログ】スプレッドシートのB列（2行目以降）から {len(master_list)} 件の持ち曲リストを読み込みました。")
        return master_list
    except Exception as e:
        print(f"【エラー】マスターリストの読み込みに失敗しました: {e}")
        return []

def normalize_for_match(s):
    s = unicodedata.normalize('NFKC', s).lower()
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
        
        timestamps = re.findall(r'\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b', line)
        if not timestamps: continue
        
        clean_line = line
        for ts in timestamps:
            clean_line = clean_line.replace(ts, "")
            
        clean_line = re.sub(r'^\d+[\.\s\-・]*', '', clean_line)
        clean_line = re.sub(r'[\(\[\{【].*?[\)\]\}】]', '', clean_line)
        clean_line = clean_line.strip(" ・-/:,[]()")
        
        if not clean_line or any(x in clean_line.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps', 'members', 'vocal:', 'drums:', 'bass:']):
            continue

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
