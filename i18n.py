"""Japanese source messages with English translations; user data stays untouched."""
import re
from string import Formatter

_language = 'ja'

EN = {
    'pypdfium2を読み込めません。依存パッケージのインストールを確認してください。': 'Cannot load pypdfium2. Check that its dependencies are installed.',
    '変更先は絶対パスで指定してください。': 'Specify absolute destination paths.',
    'DB復元・フォルダパス変更': 'Restore database / relocate folders',
    'DB全体を復元': 'Restore full database', '登録フォルダのパス変更': 'Relocate catalog folders',
    'DB保存フォルダを開く': 'Open database folder', '定期スキャン': 'Periodic scans',
    '復元できないDB形式です。': 'Unsupported database format for restoration.',
    '登録フォルダが変わりました。再確認してください。': 'Registered folders changed. Please review again.',
    '変更先のフォルダが重複しています。': 'Destination folders overlap.',
    'DB内のパスと登録フォルダが一致しません。': 'Database paths do not match their registered folders.',
    '変更先のファイルパスが重複しています。': 'Destination file paths are ambiguous.',
    'DBの整合性確認に失敗しました。': 'Database integrity check failed.',
    '復元DBが見つかりません。': 'The restored database could not be found.',
    'サムネイル・メモ・タグ・お気に入りを含むDB全体を引き継ぎます。現在のDBは残します。': 'Import the full catalog including thumbnails, notes, tags and favorites. The current database is retained.',
    'ファイル数: {v0}': 'Files: {v0}', '現在の登録パス': 'Registered path', 'このPCでのパス': 'Path on this PC',
    '選択行のフォルダを指定': 'Choose folder for selected row',
    '適用後は終了します。再起動後は定期スキャン・自動生成をOFFにしてあります。パスを確認してから再開してください。': 'The app will exit after preparation. Periodic scans and auto-generation will be off on restart. Check paths before re-enabling them.',
    '復元して終了': 'Restore and exit', 'DBのコピー・検証中…': 'Copying and validating database…',
    '復元失敗': 'Restore failed', '復元の準備完了': 'Restore prepared',
    '終了後にもう一度アプリを起動してください。以前のDB: {v0}': 'Start the app again after it exits. Previous database: {v0}',
    'バックアップ・マージ処理中…': 'Backing up and merging…',
    'メモ・タグの取り込み': 'Import notes and tags',
    '取り込み元DBの形式が不正です。': 'Unsupported source database format.',
    '対応元と対応先のフォルダを両方指定してください。': 'Specify both source and destination folders.',
    '確認後にデータが変更されました。取り込みをやり直してください。': 'Data changed after preview. Preview and import again.',
    '空欄は絶対パスで照合。別PCのDBは対応するフォルダを指定してください。': 'Leave folders blank to match absolute paths. For another PC, map the corresponding folders.',
    '取り込み元フォルダ': 'Source folder', '現在のフォルダ': 'Local folder',
    '差分を確認': 'Preview changes', 'ファイル': 'File', 'タグ（マージ後）': 'Merged tags',
    'メモの扱い': 'Note handling', '現在のメモ': 'Current note', '取り込み元のメモ': 'Imported note',
    'タグは和集合。空欄で既存メモを消しません。適用前に自動バックアップします。': 'Tags are combined. Empty notes never erase existing notes. A backup is created before applying changes.',
    'マージを適用': 'Apply merge', '閉じる': 'Close', '取り込み失敗': 'Import failed',
    '現在を保持': 'Keep current', '取り込み元を採用': 'Use imported', '両方を残す': 'Keep both',
    '空欄へ取り込み': 'Fill empty note', '変更なし': 'No change', '取り込み完了': 'Import complete',
    '照合: {v0}件 / メモ競合: {v1}件 / 対応なし・重複等: {v2}件': 'Matched: {v0} / Note conflicts: {v1} / Unmatched or ambiguous: {v2}',
    '更新: {v0}件\nバックアップ: {v1}': 'Updated: {v0}\nBackup: {v1}',
    '［空ファイル］': '[Empty file] ',
    '設定・管理': 'Settings', '検索ヘルプ': 'Search help', '絞り込み解除': 'Clear filters',
    '検索：キーワード、tag:タグ、ext:tif、-除外': 'Search: keywords, tag:label, ext:tif, -exclude',
    'すべての形式': 'All formats', '画像': 'Images', '動画': 'Videos',
    '名前順': 'Name', '更新が新しい順': 'Newest first', '更新が古い順': 'Oldest first',
    'サイズが大きい順': 'Largest first', '取得・生成タイムアウト': 'Download / generation timeout',
    'スペース区切りはAND検索、"引用符"はフレーズ検索、-語句は除外です。\nname:名前 memo:メモ tag:タグ path:パス meta:メタ情報 ext:tif type:image\n例：細胞 tag:実験 -失敗 ext:tif\n検索は保存済みDBのみを参照します。Ctrl+Fで検索欄へ移動します。': 'Separate terms with spaces for AND; use "quotes" for phrases and -term to exclude.\nFields: name: memo: tag: path: meta: ext:tif type:image\nExample: cells tag:experiment -failed ext:tif\nSearch uses the saved catalog only. Ctrl+F focuses the search box.',
    'ベクター画像を描画できません。': 'Unable to render the vector image.',
    'SVGを読み込めません。': 'Unable to read the SVG.',
    'SVGのサイズが不正です。': 'Invalid SVG dimensions.',
    '外部リンクを含むSVGは自動生成できません。画像を埋め込んで保存してください。': 'SVG with external references is not rendered automatically. Save with images embedded.',
    'PDF互換でないAIファイルには対応していません。IllustratorでPDF互換を有効にして別途保存してください。': 'AI without PDF compatibility is unsupported. Save a separate PDF-compatible copy in Illustrator.',
    'パスワード付きAIのため、自動生成をスキップしました。': 'Thumbnail generation skipped: password-protected AI.',
    'AIのPDF互換データを読み込めません。': 'Unable to read the PDF-compatible data in this AI file.',
    'サムネイルは先頭20ページまで表示します。': 'Thumbnails show the first 20 pages only.',
    'PPTの事前検査に必要なライブラリがありません。pip install -r requirements.txt を実行してください。': 'PowerPoint preflight dependency missing. Run pip install -r requirements.txt.',
    'PPTの暗号化状態を判定できないため、自動生成をスキップしました。': 'Thumbnail generation skipped: unable to determine PowerPoint encryption status.',
    'パスワード付きPPTのため、自動生成をスキップしました。': 'Thumbnail generation skipped: password-protected PowerPoint file.',
    'FFmpegが見つかりません。使用中のConda環境で conda install -c conda-forge ffmpeg を実行し、アプリを再起動してください。': 'FFmpeg was not found. Run conda install -c conda-forge ffmpeg in the active Conda environment, then restart the app.',
    '画像の画素数が設定上限を超えています。必要なら MEDIA_CATALOG_MAX_IMAGE_MP を増やしてください（既定300MP）。': 'The image exceeds the pixel limit. Increase MEDIA_CATALOG_MAX_IMAGE_MP if needed (default: 300 MP).',
    '画像の展開に必要なメモリが不足しています。他のアプリを閉じて再試行してください。': 'Not enough memory to decode this image. Close other applications and retry.',
    '言語': 'Language',
    'Media Catalog — 画像・動画・PowerPoint': 'Media Catalog — Images, Videos & PowerPoint',
    'フォルダ登録': 'Add folder', '今すぐスキャン': 'Scan now',
    '選択を再生成': 'Regenerate selected', 'DBバックアップ': 'Back up database',
    '選択候補をカタログから削除': 'Remove selected missing entries',
    'ファイル名・メモ・タグで絞り込み': 'Search filenames, notes and tags',
    '★ のみ': '★ Only', 'サブフォルダを含む': 'Include subfolders',
    '削除候補のみ': 'Missing only', 'タイムアウトのみ': 'Timed out only',
    'エラーのみ': 'Errors only', '自動生成（未キャッシュは取得）': 'Auto-generate (downloads uncached files)',
    'スキャン間隔': 'Scan interval', ' 分': ' min',
    '取得・生成タイムアウト（新しい処理から適用）': 'Download/generation timeout (new jobs)',
    'スキャン4スレッド / 生成最大3件': 'Scanning: 4 workers / Generation: up to 3',
    '表示中のタイムアウトを再試行': 'Retry visible timeouts',
    '選択フォルダの登録解除': 'Unregister folder',
    '生成を一時停止': 'Pause generation', '生成を再開': 'Resume generation',
    '生成状況を確認中…': 'Checking generation status…',
    '登録フォルダ': 'Registered folders', '詳細パネル': 'Details',
    '開く': 'Open', '終了': 'Quit', 'すべて': 'All files',
    ' ［確認不能］': ' [Unavailable]',
    '［削除候補］': '[Missing] ', '［タイムアウト］': '[Timed out] ',
    '［生成エラー］': '[Error] ', '［空のPPT］': '[Empty PPT] ',
    '［{v0}ページ］': '[{v0} pages] ', '{v0} ページ': 'Page {v0}',
    '全ページ — ': 'All pages — ', 'メタ情報・メモ — ': 'Metadata & notes — ',
    'ユーザーメモ（DBに保存）': 'Notes (saved in the catalog)',
    'タグ（カンマ区切り）': 'Tags (comma-separated)', '★ お気に入り': '★ Favorite',
    '未保存（ファイル切り替え・終了時に保存）': 'Unsaved (saved when switching files or quitting)',
    '保存済み': 'Saved', 'ファイルを選択してください。': 'Select a file to view its details.',
    '登録が解除されました。必要なメモはコピーして保管してください。': 'This file was unregistered. Copy any notes you want to keep.',
    '種類: {v0}': 'Type: {v0}', 'サイズ: {v0:,} bytes': 'Size: {v0:,} bytes',
    '更新日時: ': 'Modified: ', 'ページ数: {v0}': 'Pages: {v0}',
    'エラー: ': 'Error: ', 'なし': 'None', '状態: 削除候補': 'Status: Missing',
    '生成待ち：表示中のメタ情報は前回取得分の場合があります。': 'Generation pending: metadata may be from the previous version.',
    '\n画像メタ情報（先頭ページ）': '\nImage metadata (first page)',
    '未取得（画像は右クリックの再スキャン・再生成で取得）': 'Not collected yet. Right-click and choose Rescan / regenerate.',
    '画像メタ情報の対象外': 'Image metadata does not apply to this file type.',
    '保存失敗: ': 'Save failed: ', '保存失敗': 'Save failed',
    '未保存：登録解除または編集の競合。入力内容をコピーして保管し、アプリを開き直してください。': 'Not saved: the file was unregistered or changed by another editor. Copy your edits and reopen the app.',
    '保存できません': 'Cannot save', '未保存のメモ': 'Unsaved edits',
    'メモを保存して閉じますか？': 'Save your edits before closing?',
    '{v0:,} 件 / 削除候補 {v1:,} / タイムアウト {v2:,}': '{v0:,} files / Missing {v1:,} / Timed out {v2:,}',
    '登録するフォルダ': 'Choose a folder to register', '登録できません': 'Cannot register folder',
    'スキャンを予約しました。取得・生成とは独立して実行します。': 'Scan scheduled. Downloads and generation continue separately.',
    '削除候補': 'Missing entries',
    '削除候補の項目を選択してください。Ctrl+Aで表示中の全件を選択できます。': 'Select missing entries. Press Ctrl+A to select all visible entries.',
    'カタログから削除': 'Remove from catalog',
    '{v0}件のファイル情報・サムネイル・メタ情報・メモ・タグ・お気に入りをカタログから削除しますか？': 'Remove {v0} entries and their thumbnails, metadata, notes, tags and favorites from the catalog?',
    '元ファイルには操作しません。削除前に存在を再確認します。': 'Source files are not modified. Their absence will be checked again before removal.',
    '処理結果': 'Results',
    'カタログから削除: {v0}件\n再出現: {v1}件\n確認不能で保留: {v2}件': 'Removed from catalog: {v0}\nFound again: {v1}\nSkipped (unavailable): {v2}',
    '開けません': 'Cannot open file', '元ファイルの場所と既定アプリを確認してください。': 'Check the source file location and its default application.',
    'フォルダを開けません': 'Cannot open folder',
    '一時停止（処理中は完了まで継続）': 'Paused (active jobs will finish)',
    '自動生成OFF': 'Auto-generation off', '生成中': 'Generation enabled',
    '{v0} | 完了 {v1} / 全体 {v2} | 未完了 {v3} | タイムアウト {v4} | エラー {v5} | 処理中 {v6}\n': '{v0} | Complete {v1} / Total {v2} | Pending {v3} | Timed out {v4} | Errors {v5} | Active {v6}\n',
    '{v0} ({v1}秒)': '{v0} ({v1}s)',
    'バックアップの保存中です。': 'A backup is already in progress.',
    'DBを新しいファイルに保存': 'Save a database backup as a new file',
    'DBバックアップを保存中…': 'Saving database backup…',
    'バックアップ失敗': 'Backup failed', 'バックアップ完了': 'Backup complete',
    '\n既存ファイルは上書きしません。別の名前を指定してください。': '\nExisting files are never overwritten. Choose a different filename.',
    '元ファイルを開く': 'Open source file',
    'エクスプローラーで保存フォルダを開く': 'Show containing folder in Explorer',
    '再スキャン・再生成': 'Rescan / regenerate',
    'エクスプローラーで開く': 'Open in Explorer',
    'このフォルダ以下の登録を解除': 'Unregister this folder and its subfolders',
    '登録解除': 'Unregister folder', 'ツリーでフォルダを選択してください。': 'Select a folder in the tree.',
    'フォルダの登録解除': 'Unregister folder',
    '{v0}\n\nこのフォルダ以下の登録・サムネイル・メタ情報・メモ・タグ・お気に入りをカタログから除去します。元ファイルは削除しません。': '{v0}\n\nRemove this folder and its entries, thumbnails, metadata, notes, tags and favorites from the catalog? Source files will not be deleted.',
    '{v0}件の登録を解除しました。再登録するまでスキャン対象から外します。': 'Unregistered {v0} entries. This folder is excluded until you register it again.',
    '{v0}件を再試行予約しました。自動生成ONで順次取得・生成します。': 'Scheduled {v0} retries. Enable auto-generation and resume to process them.',
    '作業用コピーを開く既定アプリを確認してください。': 'Check the default application for opening the working copy.',
    'トレイで監視を継続します。終了はトレイメニューから選択してください。': 'Monitoring continues in the system tray. Choose Quit from the tray menu to exit.',
    '保存中': 'Saving', 'バックアップ完了後に終了してください。': 'Wait for the backup to finish before quitting.',
    '終了処理中…': 'Shutting down…',
    'すでに起動しています。タスクトレイをご確認ください。': 'Media Catalog is already running. Check the system tray.',
    'カタログから登録が解除されました。': 'This file was unregistered.',
    ' / 更新待ち（保存済みの画像を表示）': ' / Update pending (showing saved images)',
    ' / タイムアウト保留：メイン画面から再試行してください': ' / Timed out: retry from the main window',
    ' / 生成エラー：メイン画面の項目で詳細を確認してください': ' / Generation failed: see details in the main window',
    '{v0} ページ — ホバーで最大512px表示': '{v0} pages — hover for a preview up to 512px',
    # Canonical messages from the scanner and isolated thumbnail worker.
    'バックグラウンド処理エラー: ': 'Background error: ',
    'メタデータ確認中（4スレッド）: ': 'Scanning metadata: ',
    '{v0}件確認 / ': 'Checked {v0} files / ',
    '確認不能あり（左のフォルダに詳細）': 'Some folders are unavailable (see the folder tree)',
    '完了': 'Complete', '取得・処理中: ': 'Downloading / processing: ',
    '終了要求により処理を中断しました。': 'Processing stopped because the application is shutting down.',
    '取得・生成が{v0}秒でタイムアウトしました。再試行するまで保留します。': 'Download / generation timed out after {v0} seconds. Paused until retried.',
    'サムネイル生成に失敗しました。': 'Thumbnail generation failed.',
    'ページ数が不正です。': 'Invalid page count.', 'ページ数が一致しません。': 'Page count mismatch.',
    '待機中：サムネイル生成完了': 'Idle: thumbnail generation complete',
    '処理保留: ': 'Processing deferred: ',
    'アクセス可能なフォルダを指定してください。': 'Choose an accessible folder.',
    'アプリの保存領域と重なるフォルダは登録できません。': 'The selected folder overlaps the application data folder.',
    '登録済み範囲です（除外されていた場合は再登録しました）。': 'This folder is already covered (any exclusion has been cleared).',
    '登録しました。下位の登録{v0}件を統合しました。': 'Folder registered. Merged {v0} existing subfolder registrations.',
    'スキャン中断：削除判定は実施していません。': 'Scan interrupted; no missing-file detection was performed.',
    '使用中のDBは保存先にできません。': 'The active database cannot be the backup destination.',
    'バックアップの整合性確認に失敗しました。': 'Backup integrity verification failed.',
    'スライドがありません。': 'No slides.', '表示できるページがありません。': 'No displayable pages.',
    '元ファイルを出力先にはできません。': 'The source file cannot be used as the output.',
    'PowerPoint生成にはWindowsとMicrosoft PowerPointが必要です。': 'PowerPoint thumbnails require Windows and Microsoft PowerPoint.',
    '形式': 'Format', '幅 (px)': 'Width (px)', '高さ (px)': 'Height (px)',
    '色モード': 'Color mode', 'フレーム数': 'Frames', 'EXIF取得エラー': 'EXIF read error',
    '状態': 'Status', '空ファイル（0 bytes）': 'Empty file (0 bytes)', 'スライドなし': 'No slides',
}


def set_language(language):
    global _language
    _language = language if language in ('ja', 'en') else 'ja'


def get_language():
    return _language


def tr(source, **values):
    template = EN.get(source, source) if _language == 'en' else source
    return template.format(**values) if values else template


def static_source(text):
    """Recover only exact, parameter-free UI labels, never user input."""
    if text in EN:
        return text
    return _reverse.get(text, text)


_reverse = {value: key for key, value in EN.items() if '{' not in key}


def translate_message(message):
    """Translate known stored messages. Leave paths and external diagnostics intact."""
    if _language != 'en':
        return message
    if message in EN:
        return EN[message]
    for pattern, template in _messages:
        match = pattern.fullmatch(message)
        if match:
            return template.format(**match.groupdict())
    for prefix in ('バックグラウンド処理エラー: ', 'メタデータ確認中（4スレッド）: ',
                   '取得・処理中: ', '処理保留: '):
        if message.startswith(prefix):
            rest = message[len(prefix):]
            if prefix == 'バックグラウンド処理エラー: ':
                rest = translate_message(rest)
            elif prefix == '処理保留: ' and ' / ' in rest:
                filename, error = rest.rsplit(' / ',1)
                rest = filename + ' / ' + translate_message(error)
            return EN[prefix] + rest
    if '件確認 / ' in message:
        count, status = message.split('件確認 / ', 1)
        if count.replace(',', '').isdigit():
            return f'Checked {count} files / ' + translate_message(status)
    # A known application exception at the end of a traceback has a useful summary.
    last = message.strip().split('\n')[-1]
    for prefix in ('RuntimeError: ', 'ValueError: ', 'TimeoutError: '):
        if last.startswith(prefix):
            raw = last[len(prefix):]
            translated = translate_message(raw)
            if translated != raw:
                return translated
    return message


_messages = []
for source, translated in EN.items():
    if '{' in source:
        # Stored messages arrive preformatted; capture their values as strings.
        expression = ''.join(re.escape(literal) + (f'(?P<{name}>.*?)' if name else '')
                             for literal, name, spec, conversion in Formatter().parse(source))
        template = re.sub(r'\{(\w+):[^}]+\}', r'{\1}', translated)
        _messages.append((re.compile(expression, re.DOTALL), template))
