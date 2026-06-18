"""Centralized selectors for grok.com/imagine UI.

Source: DOM Inspector snapshots captured April 2026, verified at runtime.
See MASTER_grok_automation.md §7 for full reference.

Always prefix-match aria-labels (`^=`) — Grok mutates label suffixes
(e.g. "Submit prompt" vs "Submit"). Never hardcode full strings.
"""

# /imagine input page
PROMPT_INPUT = '[contenteditable="true"]'
PROMPT_INPUT_EMPTY = "p.is-empty.is-editor-empty"
SUBMIT = 'button[aria-label^="Submit"]'
UPLOAD = 'button[aria-label^="Upload"]'

# Generation mode group (anchor for Image/Video radios on /imagine)
MODE_GROUP = 'div[aria-label^="Generation mode"]'

# Image preset radios (filter by text "Speed" / "Quality")
QUALITY_RADIO = 'button[role="radio"]'

# Aspect ratio dropdown
ASPECT_TRIGGER = 'button[aria-label^="Aspect Ratio"]'
ASPECT_OPTION = 'div[role="menuitem"]'

# Video-only groups (only visible when mode=Video)
VIDEO_RES_GROUP = 'div[aria-label="Video resolution"]'
VIDEO_DUR_GROUP = 'div[aria-label="Video duration"]'

# Image generation lifecycle (within masonry section)
MASONRY_PREFIX = '[id^="imagine-masonry-section-"]'
LIST_ITEM = '[role="listitem"]'

# /imagine/post/{uuid} result page
DOWNLOAD = 'button[aria-label^="Download"]'
REDO_IMAGE = 'button[aria-label^="Redo image"]'
MAKE_VIDEO = 'button[aria-label^="Make video"]'
PLAY = 'button[aria-label^="Play"]'
BACK = 'div[aria-label^="Back"]'  # NOTE: <div>, not <button>
VIDEO_ELEMENT = "#sd-video"  # presence = video ready

# Toasts (errors)
TOAST = "[data-sonner-toast]"

# Upload popup (after clicking Upload button)
UPLOAD_POPUP_BTN = 'button:has-text("Upload or drop images")'
FILE_INPUT = 'input[type="file"]'
