import os
import re
import csv
import json
import urllib.request
from datetime import datetime

# GitHub Actionsのシークレットから取得する想定
API_KEY = os.environ.get("YOUTUBE_API_KEY", "YOUR_API_KEY_HERE")
CHANNEL_ID = "YOUR_CHANNEL_ID_HERE" # 後で設定します

def load_master_songs(csv_path):
    """マスターリスト(CSV)から曲名のリストを読み込む"""
    songs = []
    if not os.path.exists(csv_path):
        return songs
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if '楽曲名' in row and row['楽曲名'].strip():
                songs.append(row['楽曲名'].strip())
    return songs

def clean_song_title(raw_title):
    """ノイズを除去して純粋な曲名にする"""
    # タイムスタンプ、ハイフン、カッコなどを除去
    title = re.sub(r'\d{1,2}:\d{2}:\d{2}|\d{1,2}:\d{2}', '', raw_title)
    title = re.sub(r'^[-\s]+', '', title)
    # アンコール表記などを除去
    title = re.sub(r'\(?アンコール曲?\)?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'encore', '', title, flags=re.IGNORECASE)
    return title.strip()

def analyze_description(description, date_str, master_songs, data_store):
    """概要欄のテキストを解析して集計する"""
    lines = description.split('\n')
    is_encore_mode = False

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # アンコール判定 (単独行の「アンコール」)
        if line.lower() == 'アンコール' or line.lower() == 'encore':
            is_encore_mode = True
            continue

        # タイムスタンプが含まれる行かチェック (例: 00:00 または 1:00:00)
        if not re.search(r'\d{1,2}:\d{2}', line):
            continue

        # ノイズ除外 (intro, greeting, mc など)
        lower_line = line.lower()
        if any(ignore in lower_line for ignore in ['intro', 'greeting', 'mc', 'オープニング', 'エンディング']):
            continue

        # アンコール判定 (行内に含まれる場合)
        is_encore_line = is_encore_mode or ('アンコール' in line) or ('encore' in lower_line)

        # 曲名を抽出
        raw_title = clean_song_title(line)
        if not raw_title:
            continue

        # マスターデータとの照合 (完全一致だけでなく、含まれるかでざっくり判定)
        matched_song = None
        for master in master_songs:
            if master.lower() in raw_title.lower():
                matched_song = master
                break
        
        target_dict = data_store['encores'] if is_encore_line else data_store['main']

        if matched_song:
            # 既知の曲
            if matched_song not in target_dict:
                target_dict[matched_song] = {'count': 0, 'lastPlayed': date_str}
            target_dict[matched_song]['count'] += 1
            # 日付が新しい場合は更新
            if date_str > target_dict[matched_song]['lastPlayed']:
                target_dict[matched_song]['lastPlayed'] = date_str
        else:
            # 未登録曲
            if raw_title not in data_store['unknown']:
                data_store['unknown'].append(raw_title)

def main():
    master_songs = load_master_songs('master_songs.csv')
    
    data_store = {
        'main': {},
        'encores': {},
        'unknown': []
    }

    # ----- ここにYouTube API経由で動画リストと概要欄を取得する処理が入ります -----
    # ※ 実際の稼働時は API_KEY を使ってデータを取得しますが、
    # スクリプトの骨格として用意しています。
    
    # テスト用ダミーデータでの処理シミュレーション
    dummy_description = """
    00:00 intro
    02:30 Born in Chicago
    06:10 横浜ホンキートンクブルース
    12:00 MC
    アンコール
    15:00 Got My Mojo Workin' (アンコール曲)
    20:00 知らない新曲
    """
    analyze_description(dummy_description, "2026-05-20", master_songs, data_store)

    # データをJSON用に整形
    output = {
        "lastUpdated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, "count": v['count'], "lastPlayed": v['lastPlayed']} for k, v in data_store['main'].items()],
        "encores": [{"name": k, "count": v['count'], "lastPlayed": v['lastPlayed']} for k, v in data_store['encores'].items()],
        "unknown": data_store['unknown']
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("✅ data.json generated successfully.")

if __name__ == "__main__":
    main()
