import os
import re
import csv
import json
from datetime import datetime
from googleapiclient.discovery import build

# GitHubのSecretsから自動で読み込まれます
API_KEY = os.environ.get("YOUTUBE_API_KEY")
HANDLE = "@70315"

def load_master_songs(csv_path):
    """CSVから曲名のリストを読み込む（1列/多列、ヘッダー有無すべて対応）"""
    songs = []
    if not os.path.exists(csv_path):
        print(f"ファイルが見つかりません: {csv_path}")
        return songs
    
    # utf-8-sig で文字化け(BOM)を防ぐ
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        
        name_idx = 0 # デフォルトは1列目(0)
        
        if headers:
            if '楽曲名' in headers:
                name_idx = headers.index('楽曲名')
            else:
                # ヘッダーではなく1曲目のデータだった場合はリストに追加
                song = headers[name_idx].strip()
                if song:
                    songs.append(song)

        for row in reader:
            if len(row) > name_idx and row[name_idx].strip():
                songs.append(row[name_idx].strip())
                
    return songs

def clean_song_title(raw_title):
    """ノイズを除去して純粋な曲名にする"""
    title = re.sub(r'\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2}', '', raw_title)
    title = re.sub(r'^[-\s]+', '', title)
    title = re.sub(r'\(?アンコール曲?\)?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'encore', '', title, flags=re.IGNORECASE)
    return title.strip()

def analyze_description(description, date_str, master_songs, data_store):
    """概要欄のテキストを解析して集計する"""
    lines = description.split('\n')
    is_encore_mode = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # アンコール判定 (単独行)
        if line.lower() == 'アンコール' or line.lower() == 'encore':
            is_encore_mode = True
            continue

        # タイムスタンプが含まれる行か
        if not re.search(r'\d{1,2}:\d{2}', line):
            continue

        # ノイズ除外 (MCなど)
        lower_line = line.lower()
        if any(ignore in lower_line for ignore in ['intro', 'greeting', 'mc', 'オープニング', 'エンディング', 'トーク']):
            continue

        is_encore_line = is_encore_mode or ('アンコール' in line) or ('encore' in lower_line)
        raw_title = clean_song_title(line)
        if not raw_title:
            continue

        # マスターデータとの照合（表記揺れ対策を強化）
        matched_song = None
        for master in master_songs:
            # 比較する時は、両方のスペースを消して小文字に統一して判定する
            clean_master = master.replace(' ', '').replace('　', '').lower()
            clean_raw = raw_title.replace(' ', '').replace('　', '').lower()
            
            if clean_master in clean_raw or clean_raw in clean_master:
                matched_song = master
                break
        
        target_dict = data_store['encores'] if is_encore_line else data_store['main']

        if matched_song:
            if matched_song not in target_dict:
                target_dict[matched_song] = {'count': 0, 'lastPlayed': date_str}
            target_dict[matched_song]['count'] += 1
            if date_str > target_dict[matched_song]['lastPlayed']:
                target_dict[matched_song]['lastPlayed'] = date_str
        else:
            if raw_title not in data_store['unknown']:
                data_store['unknown'].append(raw_title)

def main():
    if not API_KEY:
        print("エラー: YOUTUBE_API_KEY が見つかりません。")
        return

    youtube = build('youtube', 'v3', developerKey=API_KEY)
    print(f"チャンネル {HANDLE} の情報を取得中...")
    
    channel_res = youtube.channels().list(
        part="contentDetails",
        forHandle=HANDLE
    ).execute()
    
    if not channel_res.get("items"):
        print("指定されたチャンネルが見つかりませんでした。")
        return
        
    uploads_playlist_id = channel_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    
    videos = []
    next_page_token = None
    print("動画リストを抽出中...")
    while True:
        playlist_res = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        
        videos.extend(playlist_res.get("items", []))
        next_page_token = playlist_res.get("nextPageToken")
        if not next_page_token:
            break

    # 鈴木さんがアップロードしたCSVファイルを読み込む
    master_songs = load_master_songs('master_songs.csv')
    print(f"マスターデータとして {len(master_songs)} 曲を読み込みました。")
    
    data_store = {
        'main': {},
        'encores': {},
        'unknown': []
    }

    print(f"全{len(videos)}件の動画からライブ演奏データを集計します...")
    for video in videos:
        snippet = video["snippet"]
        title = snippet["title"]
        description = snippet["description"]
        
        if not re.search(r'\d', title):
            continue
            
        published_at = snippet["publishedAt"]
        date_str = published_at.split('T')[0]
        
        analyze_description(description, date_str, master_songs, data_store)

    output = {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, "count": v['count'], "lastPlayed": v['lastPlayed']} for k, v in data_store['main'].items()],
        "encores": [{"name": k, "count": v['count'], "lastPlayed": v['lastPlayed']} for k, v in data_store['encores'].items()],
        "unknown": data_store['unknown']
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        
    print("✅ data.json の生成が完了しました！")

if __name__ == "__main__":
    main()
