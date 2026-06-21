import os
import re
import json
import unicodedata
import requests
import csv
import io
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

API_KEY = os.environ.get("YOUTUBE_API_KEY")
CHANNEL_HANDLE = "@70315"  # チャンネルのハンドル（@から始まる形式）
CSV_URL = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vTzWOELNEzNkvAb1Nld1Tjzv0_Z5mvRvuQdeH20jy-LYP0cycHgpWcpR6rcSBFqm-5lGKZYLgSmW4cg/pub?gid=842461559&single=true&output=csv'

def load_master_songs_from_web(csv_url):
    try:
        response = requests.get(csv_url)
        response.encoding = 'utf-8-sig'
        f = io.StringIO(response.text)
        reader = csv.reader(f)
        songs = []
        
        # 【修正】1行目（ヘッダー行）は文字の有無に関わらずスキップ
        try:
            next(reader)
        except StopIteration:
            return []
            
        # 【修正】2行目以降から、固定でB列（インデックス 1）の値を直接読み込む
        for row in reader:
            if not row: continue
            if len(row) > 1:  # B列が存在する行のみ対象
                song = row[1].strip()  # インデックス1 ＝ B列
                if song and not song.isdigit():
                    songs.append(song)
        
        master_list = list(set(songs))
        print(f"【ログ】スプレッドシートのB列（2行目以降）から {len(master_list)} 件の持ち曲リストを読み込みました。")
        return master_list
    except Exception as e:
        print(f"【エラー】マスターリストの読み込みに失敗しました: {e}")
        return []

def normalize_for_match(s):
    """
    YouTube概要欄のアバウトな表記を許容するため、
    大文字小文字・半角全角・スペース・あらゆる記号を消し去って統一化する関数
    """
    s = unicodedata.normalize('NFKC', s).lower()
    # 空白、スラッシュ、コロン、カッコ、各種記号をすべて消去
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
        
        # 1. タイムスタンプ（MM:SS等）を抽出
        timestamps = re.findall(r'\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b', line)
        if not timestamps: continue
        
        # 2. 行からタイムスタンプを消去
        clean_line = line
        for ts in timestamps:
            clean_line = clean_line.replace(ts, "")
            
        # 3. 先頭の「1.」「02.」などの曲番や不要な記号をお掃除
        clean_line = re.sub(r'^\d+[\.\s\-・]*', '', clean_line)
        # 後ろにくっついている「(Live)」「[Guitar solo]」などのカッコ書きを自動除去して許容する
        clean_line = re.sub(r'[\(\[\{【].*?[\)\]\}】]', '', clean_line)
        clean_line = clean_line.strip(" ・-/:,[]()")
        
        # 4. ノイズ行のスキップ
        if not clean_line or any(x in clean_line.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps', 'members', 'vocal:', 'drums:', 'bass:']):
            continue

        # 5. 照合ロジック（アバウトな概要欄を許容する部分一致）
        matched_song = None
        clean_raw = normalize_for_match(clean_line)
        
        for master in master_songs:
            m_norm = normalize_for_match(master)
            # 概要欄に持ち曲名が含まれているか、または持ち曲名に概要欄の文字が含まれていれば「一致」と判定
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

    # 【追加】キーが読み込めているかどうかだけを安全に確認（値そのものは出力しない）
    print(f"【ログ】YOUTUBE_API_KEY 読み込み確認: 設定済み（{len(API_KEY)}文字）")

    try:
        youtube = build('youtube', 'v3', developerKey=API_KEY)

        print("【ログ】YouTubeから動画リストを取得中...")
        channel_res = youtube.channels().list(part="contentDetails", forHandle=CHANNEL_HANDLE).execute()

        # 【追加】itemsが無い/空の場合に、具体的な状況を出力する
        items = channel_res.get("items")
        if not items:
            print(f"【エラー】チャンネル情報が取得できませんでした。CHANNEL_HANDLE が正しいか確認してください。レスポンス全体: {json.dumps(channel_res, ensure_ascii=False)[:500]}")
            return

        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

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

    except HttpError as e:
        # 【追加】HttpErrorの場合は、HTTPステータスコードと理由を具体的に出力する
        # （クォータ切れ／APIキー無効／リファラー制限／APIが有効化されていない、等を見分けるための情報）
        status = getattr(e.resp, "status", "不明")
        try:
            error_detail = json.loads(e.content.decode("utf-8")) if e.content else {}
        except Exception:
            error_detail = {"raw": str(e.content)}
        print(f"【エラー】YouTube APIがエラーを返しました。HTTPステータス: {status}")
        print(f"【エラー詳細】{json.dumps(error_detail, ensure_ascii=False)}")
        return
    except Exception as e:
        # 【変更】「クォータ切れの可能性あり」という決め打ちの文言は外し、実際の例外の型と内容をそのまま出す
        print(f"【エラー】YouTube APIからのデータ取得中に予期しないエラーが発生しました（種類: {type(e).__name__}）: {e}")
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
