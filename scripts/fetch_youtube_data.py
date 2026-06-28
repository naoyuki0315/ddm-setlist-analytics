import os
import re
import json
import unicodedata
import requests
import csv
import io
import difflib
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

def timestamp_to_seconds(ts):
    """ "12:34" や "1:02:34" のようなタイムスタンプ文字列を、秒数（int）に変換する """
    parts = [int(p) for p in ts.split(':')]
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds

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
        # 【変更】"working"だけでなく "walking" 等も含めて、-ing と -in' のゆらぎを汎用的に統一する
        'ing': 'in',
        'allright': 'alright',
    }
    for key, val in replacements.items():
        s = s.replace(key, val)
    return s

def analyze_description(description, date_str, video_id, master_songs, data_store):
    if not description: return

    lines = description.split('\n')
    # 【変更】「アンコール」という文字が出てきた行以降は、最後までアンコール扱いにする
    # （見出し行「---アンコール---」と曲名の行が別々になっている概要欄に対応するため）
    in_encore_section = False

    for line in lines:
        line = line.strip()

        if 'アンコール' in line.lower() or 'encore' in line.lower():
            in_encore_section = True

        # 1. タイムスタンプ（MM:SS等）を抽出
        timestamps = re.findall(r'\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b', line)
        if not timestamps: continue
        
        # 2. 行からタイムスタンプを消去
        clean_line = line
        for ts in timestamps:
            clean_line = clean_line.replace(ts, "")
            
        # 3. 先頭の「1.」「02.」などの曲番や不要な記号をお掃除
        clean_line = re.sub(r'^\d+[\.\s\-・]*', '', clean_line)
        # 【追加】「リクエスト」「Request」などの前置きラベルを除去
        clean_line = re.sub(r'^(リクエスト|request)[\s：:　]*', '', clean_line, flags=re.IGNORECASE)
        # 後ろにくっついている「(Live)」「[Guitar solo]」「（アンコール曲）」などのカッコ書きを自動除去して許容する
        clean_line = re.sub(r'[\(\[\{【（].*?[\)\]\}】）]', '', clean_line)
        clean_line = clean_line.strip(" ・-/:,[]()")
        
        # 4. ノイズ行のスキップ（【修正】英語の「members」は実在の持ち曲『Members Only』を巻き込んで毎回消してしまっていたため除外。日本語の「メンバー」は残す）
        if not clean_line or any(x in clean_line.lower() for x in ['intro', 'greeting', 'mc', 'トーク', 'timestamps', 'メンバー', 'vocal:', 'drums:', 'bass:']):
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

        # 【追加】5b. 部分一致でも見つからない場合、タイプミス等を許容するあいまい一致にフォールバック
        if not matched_song and len(clean_raw) >= 4:
            best_ratio, best_master = 0, None
            for master in master_songs:
                m_norm = normalize_for_match(master)
                if not m_norm: continue
                ratio = difflib.SequenceMatcher(None, clean_raw, m_norm).ratio()
                if ratio > best_ratio:
                    best_ratio, best_master = ratio, master
            if best_ratio >= 0.85:
                matched_song = best_master

        target_dict = data_store['encores'] if in_encore_section else data_store['main']

        # 【追加】そのタイムスタンプの秒数を計算し、再生位置付きのURLを作る
        ts_seconds = timestamp_to_seconds(timestamps[0]) if timestamps else 0
        video_url = f"https://www.youtube.com/watch?v={video_id}&t={ts_seconds}s"

        if matched_song:
            if matched_song not in target_dict:
                target_dict[matched_song] = {'count': 0, 'lastPlayed': '', 'playDates': [], 'urls': []}
            target_dict[matched_song]['count'] += 1
            target_dict[matched_song]['playDates'].append(date_str)
            target_dict[matched_song]['urls'].append({'date': date_str, 'url': video_url})
            target_dict[matched_song]['lastPlayed'] = max(target_dict[matched_song]['lastPlayed'], date_str)
        else:
            # 【変更】持ち曲以外（未一致）も、main/encoresと同じ形で日付・動画リンクを記録する
            if len(clean_line) > 2:
                if clean_line not in data_store['unknown']:
                    data_store['unknown'][clean_line] = {'count': 0, 'lastPlayed': '', 'playDates': [], 'urls': []}
                data_store['unknown'][clean_line]['count'] += 1
                data_store['unknown'][clean_line]['playDates'].append(date_str)
                data_store['unknown'][clean_line]['urls'].append({'date': date_str, 'url': video_url})
                data_store['unknown'][clean_line]['lastPlayed'] = max(data_store['unknown'][clean_line]['lastPlayed'], date_str)

def main():
    # 【追加】このログが出れば「新しいコードが実際に実行された」ことの確実な証拠になる
    print("【ログ】スクリプトバージョン: v3（unknownもオブジェクト形式・アンコール判定ステートフル・あいまい一致対応）")

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

        channel_id = items[0]["id"]
        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        video_id_set = set()

        # 【方法1】アップロード一覧プレイリストから取得
        next_page_token = None
        page_count_a = 0
        while True:
            playlist_res = youtube.playlistItems().list(part="contentDetails", playlistId=uploads_playlist_id, maxResults=50, pageToken=next_page_token).execute()
            for item in playlist_res.get("items", []):
                vid = item.get("contentDetails", {}).get("videoId")
                if vid:
                    video_id_set.add(vid)
            next_page_token = playlist_res.get("nextPageToken")
            page_count_a += 1
            if not next_page_token or page_count_a >= 40:
                break
        print(f"【ログ】方法1（アップロード一覧）から {len(video_id_set)} 件の動画IDを取得しました。（{page_count_a}ページ）")

        # 【方法2】search.list からも取得し、方法1で漏れている分（ライブ配信アーカイブ等）を補う
        before_count = len(video_id_set)
        next_page_token = None
        page_count_b = 0
        while True:
            search_res = youtube.search().list(
                channelId=channel_id,
                part="id",
                type="video",
                order="date",
                maxResults=50,
                pageToken=next_page_token
            ).execute()
            for item in search_res.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid:
                    video_id_set.add(vid)
            next_page_token = search_res.get("nextPageToken")
            page_count_b += 1
            if not next_page_token or page_count_b >= 40:
                break
        print(f"【ログ】方法2（検索API）を合体させ、{len(video_id_set) - before_count} 件を追加で発見しました。（{page_count_b}ページ）合計: {len(video_id_set)} 件")

        video_ids = list(video_id_set)

        # 【追加】動画IDを50件ずつまとめて、概要欄（description）の全文を取得する
        videos = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i+50]
            videos_res = youtube.videos().list(part="snippet", id=",".join(batch)).execute()
            videos.extend(videos_res.get("items", []))

        print(f"【ログ】{len(videos)} 件の動画の詳細情報を取得しました。")

        # 【デバッグ用・一時的】取得できた動画のうち、投稿日が新しい方から5件を表示する
        # （最近の動画が本当に取得できているか確認するためのログ。確認できたら削除してOK）
        sorted_recent = sorted(videos, key=lambda v: v.get("snippet", {}).get("publishedAt", ""), reverse=True)[:5]
        print("【デバッグ】取得した動画のうち、投稿日が新しい方から5件:")
        for v in sorted_recent:
            pub = v.get("snippet", {}).get("publishedAt", "不明")
            title = v.get("snippet", {}).get("title", "不明")
            vid = v.get("id", "不明")
            print(f"  - {pub} | {title} | id={vid}")

        # 【デバッグ用・一時的】6/28の動画の概要欄をそのまま表示して、改行(\n)が入っているか確認する
        target_check_id = "szIyo5E5QyQ"
        target_video = next((v for v in videos if v.get("id") == target_check_id), None)
        if target_video:
            print(f"【デバッグ】対象動画({target_check_id})の概要欄全文 ↓↓↓")
            print(repr(target_video.get("snippet", {}).get("description", "")))
            print("【デバッグ】対象動画の概要欄 ↑↑↑ ここまで")
        else:
            print(f"【デバッグ】対象動画({target_check_id})はvideosリストの中に見つかりませんでした。")

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
    data_store = {'main': {}, 'encores': {}, 'unknown': {}}

    print("【ログ】動画説明欄の解析を開始します...")
    for video in videos:
        try:
            snippet = video.get("snippet", {})
            desc = snippet.get("description", "")
            pub_at = snippet.get("publishedAt", "2000-01-01T00:00:00Z").split('T')[0]
            # 【変更】videos().list() のレスポンスでは、動画IDはトップレベルの "id" に直接入っている
            v_id = video.get("id", "")
            if v_id:
                analyze_description(desc, pub_at, v_id, master_songs, data_store)
        except Exception as e:
            # 【変更】今までエラーを表示せず黙ってスキップしていたが、原因が分かるようにログを出す
            print(f"【エラー】動画(id={video.get('id', '不明')})の解析中にエラーが発生しました（この動画はスキップされました）: {type(e).__name__}: {e}")
            continue

    output = {
        "lastUpdated": datetime.now(timezone(timedelta(hours=+9))).strftime("%Y-%m-%d %H:%M"),
        "main": [{"name": k, **v} for k, v in data_store['main'].items()],
        "encores": [{"name": k, **v} for k, v in data_store['encores'].items()],
        "unknown": [{"name": k, **v} for k, v in data_store['unknown'].items()]
    }
    try:
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"✅ 集計完了！ファイル保存成功 main: {len(output['main'])}件, encores: {len(output['encores'])}件, unknown: {len(output['unknown'])}件")
    except Exception as e:
        print(f"【エラー】data.jsonの保存に失敗しました: {e}")

if __name__ == "__main__":
    main()
