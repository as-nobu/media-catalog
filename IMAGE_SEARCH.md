# Image search

This extension targets the attached Media Catalog revision
`595f7d4eafecd1c9e6f26bb09d55d501c81e4be7`. It searches existing SQLite thumbnails
using a selected screen region, without opening catalog originals.

## Install

Quit the app completely, including its tray process. In the repository:

```sh
git apply --check media-catalog-image-search.patch
git apply media-catalog-image-search.patch
```

After applying the previous image-search patch, apply the automatic-indexing update:

```sh
git apply --check media-catalog-auto-features.patch
git apply media-catalog-auto-features.patch
```

Then apply the on-demand model-download update:

```sh
git apply --check media-catalog-auto-model.patch
git apply media-catalog-auto-model.patch
```

In the app's active Conda environment:

```sh
conda install --override-channels -c conda-forge opencv
python app.py
```

For pip environments, use `python -m pip install -r requirements.txt`.
For the setup.bat venv, use `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
Install only one OpenCV distribution in an environment: do not combine Conda
opencv with pip OpenCV or install multiple pip OpenCV variants.

## Use

Click **Image search** beside the text search field to expand the normally hidden
search panel. Opening it does not capture the screen, start a search or download a model.
Click **Select region** inside the panel; the app temporarily hides its windows.
Drag across the desired screen region and release to search; Esc or
right-click cancels. Results appear directly in the main catalog, with its
existing thumbnail view, details panel, notes, tags and context menu. Screenshots
remain in memory only. Each file appears once, showing its best matching page.
Hover to enlarge that cached page; the details panel includes the matching page,
similarity and inlier count. Opening an original remains an explicit action and
may download it.

**Back to catalog** restores the previous catalog filters. **Show search results**
returns to the saved results without another search. Selecting another candidate
does not dismiss the results. Results are revalidated against current catalog
records when the view refreshes.

Buttons and the two search options share one row, beside a small query preview;
status and result counts appear underneath. Click **Image search** again to collapse the controls.
The query, settings and results are retained; an active search continues.
Reopening does not capture or search again. Every app launch starts with the panel hidden.

- Search uses **DINOv2 plus SIFT** exclusively. The former Standard preference is
  ignored. OpenCV remains required for local-feature verification.
- **Within current catalog filters** uses the files displayed when Search is
  pressed. By default, all active catalog registrations are searched.
- **Verify partial matches in every page (slow)** is OFF by default. Normal
  search compares all global vectors and verifies only the top 300 pages.
  Enable the option for exhaustive local matching; it can recover crops outside
  the shortlist but is considerably slower. The old automatically saved
  exhaustive default is not inherited; this option uses a new explicit setting.
- At most 100 distinct files are shown, with their best matching page. Local
  geometric matches rank first; similarity and inliers do not prove identity.
- **Cancel** retains completed index entries and the previous search results.

### Automatic feature extraction

- At startup, the app checks missing DINO features and shows page
  counts in a Yes/No confirmation (default No). For the existing backlog, model
  download/inference starts only after Yes. When the model is missing, all cached pages are counted for DINO;
  actual existing feature entries are rechecked after the download.
- No defers the existing backlog until another launch or an explicitly started
  search. It does not prevent automatic indexing of newly generated thumbnails.
- Successfully saved new/regenerated thumbnails enqueue all their cached pages
  for DINO only. Completed features are reused. Only the newly saved files enter
  this automatic queue; no new Standard features are extracted.
- DINO requires installed packages. Missing models are downloaded on demand:
  after startup consent, during new-thumbnail indexing, or for an AI search.
  Complete local models are reused without network access. Missing packages or
  broken models defer processing, with no fallback to Standard. Python packages are
  never installed automatically; restart after installing them.
- While processing, the main window shows status and **Cancel feature extraction**.
  Normal completion, idle and cancelled states are hidden. Actionable dependency,
  download and indexing errors remain visible. Cancellation
  retains completed entries; subsequent new thumbnails still enqueue work.
  Thumbnail-generation pause and feature-extraction cancellation are separate.
- An explicitly started search also fills missing DINO features,
  even if startup indexing was declined. Corrupt thumbnail errors are
  recorded, avoiding repeated startup prompts.

Indexing reads only SQLite thumbnails and local model files, never originals.

## AI setup

Image search requires the AI packages. Existing AI users need not reinstall them.
Activate the app's Conda environment and install the packages:

```sh
conda install --override-channels -c conda-forge "pytorch-cpu>=2.2,<3" "transformers>=4.40,<5" "safetensors>=0.4,<1" "huggingface_hub>=0.23,<1"
```

Alternatively, for pip environments (use `.venv\Scripts\python.exe` for the venv):

```sh
python -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-image-ai.txt
```

Models are downloaded automatically when needed. The default destination is
`models/dinov2-small`; a custom folder saved in a previous version takes precedence.
The compact panel has no engine label, model-folder editor, Browse, Rebuild or
Close button. Existing model locations and extracted features are reused.
You can still run `python download_search_model.py` to pre-download the model.
Model weights occupy about 88 MB; runtime libraries
require additional space. The engine uses CPU; a GPU is not required. You can
copy a downloaded model directory from another PC. A download status is shown;
cancel or quit terminates the downloader. Failure/cancellation defers further
automatic attempts for that session; restart or start an AI search to retry.
Incomplete files are retained for retry, and never loaded as a complete model.
Download timeout is 15 minutes. Downloads contact Hugging Face's model servers;
images, thumbnails and the database are never uploaded. Loading is local-only, safetensors-only,
with remote Python code disabled. The download script pins revision
`ed25f3a31f01632728cabb09d1542f84ab7b0056` of
[facebook/dinov2-small](https://huggingface.co/facebook/dinov2-small)
(Apache License 2.0).

## Limits and storage

Search data is stored in the existing SQLite database, with registration ID,
thumbnail revision, page, engine version and AI model fingerprint. Startup after
confirmation, successful thumbnail generation, and explicit searches update
new/changed pages in a worker thread. Existing DINO features keep their format and
model fingerprint, including partially completed indexes. Legacy Standard entries
are left intact; their absence never triggers an extraction prompt.
Full backup/restore includes the index; compact annotation export
does not. Adding the schema does not queue thumbnail regeneration.

Only cached pages are searched: up to 20 pages per multipage file, and only the
saved representative frame of a video. Missing thumbnails are counted and
skipped, never fetched. Corrupt cached pages are counted.
Old thumbnails pending regeneration can still be searched. Files marked missing
or unregistered are excluded. Search uses catalog metadata, not filesystem probes.
Deleted, re-registered and regenerated result entries are filtered on refresh. Fine details lost at thumbnail resolution
cannot be recovered. Whole-image and four overlapping region features improve
cropped-image search but cannot guarantee arbitrary crop recall. Repeated cell
patterns, severe edits or tiny pictures inside slides can give poor matches.
Nearest candidates are returned even when the desired image is absent.

Decoded existing vectors are reused in RAM (one engine/database, approximately
512 MiB maximum). The first search after launch or a relevant DB change loads
vectors; subsequent searches reuse them. Larger collections fall back to bounded
streaming. This changes neither the stored vector format nor the model identity.
Large catalogs take more time and space, especially with exhaustive matching.
Cancellation/exit can wait for the current image operation to finish.
The existing automatic thumbnail generator is independent and may still
download originals while search is running. Disable automatic generation and
wait for active jobs to finish if you need the entire app to stop retrieval.

Tests cover offline source access, cropped/resized/rotated images, cached pages,
incremental indexing, cancellation, lifecycle races, backup, mixed-DPI coordinate
composition, selection gestures, GUI navigation and shutdown. Development QA
also covers startup consent/refusal, new generation after refusal, DINO-only
indexing, missing/broken DINO, hidden idle status, explicit capture, panel
hide/reopen, and shutdown with an unanswered confirmation. QA
uses Linux/headless Qt; actual Windows monitors and Box Drive require testing
on the target PC.

```sh
python -m unittest discover -s tests -p "test_*.py"
python tests/smoke_image_search.py
```

Set `MEDIA_CATALOG_TEST_DINO` to the local model directory to enable the optional
offline real-model test.
Set `MEDIA_CATALOG_TEST_DOWNLOAD=1` to enable the actual network download test.
