# Media Catalog

[日本語](README.ja.md) · English

A Windows app for browsing images, videos, TIFFs and PowerPoint files as a local thumbnail catalog.

## Get started

1. Install **Python 3.12 (64-bit)** with the Windows Python launcher.
2. Download and extract the app, then open the folder containing `app.py`.
3. Run `setup.bat`, then `start.bat`.
4. Select **Add folder** and choose the files you want to catalog.

Video thumbnails require **FFmpeg** on PATH. PowerPoint thumbnails require **Microsoft PowerPoint for Windows**.

### Anaconda / Miniconda

Use Anaconda Prompt in the app folder instead of `setup.bat`:

```shell
conda create -n media-catalog python=3.12
conda activate media-catalog
conda install -c conda-forge ffmpeg
python -m pip install -r requirements.txt
python app.py
```

## Everyday use

| Action | How |
|---|---|
| Change language | Choose **日本語 / English** at the top. The setting is remembered. |
| Browse a folder | Select it in the left tree; optionally include subfolders. |
| Preview pages | Hover over a thumbnail. TIFF and PowerPoint pages appear together in a tiled preview. |
| Open a file | Double-click it, or use the right-click menu. |
| Find files | Search filenames, notes and tags; filter favorites, missing entries, errors or timeouts. |
| Add notes and tags | Edit the right-hand panel. Separate tags with commas. |
| Show or resize details | Toggle **Details**, or drag the panel divider. |
| Pause generation | Select **Pause generation**. Active jobs finish; folder scans continue. |
| Retry a file | Right-click and choose **Rescan / regenerate**. Auto-generation must be on and resumed. |
| Back up | Select **Back up database** and save to a new file. |
| Quit | Right-click the tray icon and select **Quit**. Closing the window normally hides it in the tray. |

Notes, tags and favorites are saved when you switch files, hide the app in the tray, quit, or start a backup. Language switching preserves unsaved edits.

## Cloud folders and your files

**Auto-generation is on by default.** Generating a thumbnail may download an uncached Box Drive file. Turn auto-generation off before adding a folder if you only want to list its files. Browsing saved thumbnails does not download the originals.

Scans compare file size and modification time. This assumes your cloud client can provide that information without downloading file contents. Changes made on another PC are detected after they reach the local cloud client.

The app does not modify or delete original files. Missing files are marked for review; removing catalog entries or unregistering a folder also removes their saved notes, tags and thumbnails. Opening an original in another app allows editing there.

## Troubleshooting

- **FFmpeg not found:** activate the environment used to run the app, run `conda install -c conda-forge ffmpeg`, then restart and retry. For a separate installation, add FFmpeg to PATH or set `MEDIA_CATALOG_FFMPEG` to its executable.
- **Large image:** the default limit is **300 megapixels per page**. Large images also need sufficient RAM. If necessary, set `MEDIA_CATALOG_MAX_IMAGE_MP` before launching; the value is in megapixels.
- **Timeout:** the default is 30 minutes. Increase it for slow downloads, then retry the affected files.
- **Backup:** use the app's backup command while it is running. Backups include the catalog and thumbnails, not original files. To restore, fully quit, set aside the current data folder, and place the backup there as `catalog.sqlite3`; do not reuse old `-wal` or `-shm` files.
- **Data location:** a local per-user MediaCatalog folder under `%LOCALAPPDATA%`. Updating the app keeps the catalog.

Native Windows dialogs and messages from external applications may follow the OS language. Windows / Box Drive / PowerPoint behavior is not fully tested on the release build environment.

## License

[MIT License](LICENSE). External applications and dependencies retain their own licenses.
