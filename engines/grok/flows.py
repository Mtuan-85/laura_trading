"""Declarative flow definitions for grok.com/imagine.

Steps are dicts; param resolution by the runner:
    from_prompt: <key>      → state["current_prompt"][key]
    from_config: <key>      → config[key]
    from_var: <name>        → state["vars"][name]   (supports "foo.bar")
    masonry_from_var: <key> → resolved like from_var (special-cased click_image)
    value: <literal>        → use as-is

Auto-selection (engine.py):
    image, no ref → text_to_image
    image, ref    → image_to_image
    video, no ref → text_to_video
    video, ref    → image_to_video

For the story_video_maker workflow, gen_video is always image_to_video
(every video starts from a generated still). text_to_video stays here for
completeness / future use.
"""

FLOWS: dict[str, dict] = {
    "text_to_image": {
        "name": "Text-to-Image",
        "loop_per_prompt": True,
        "steps": [
            {"action": "ensure_at", "url": "/imagine"},
            {"action": "set_mode", "value": "image"},
            {"action": "set_quality", "from_config": "quality"},
            {"action": "set_aspect", "from_config": "aspect"},
            {"action": "fill_prompt", "from_prompt": "text"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1200},
            {
                "action": "submit_and_wait_ready",
                "target_count_from_config": "target_count",
                "save_to": "ready_result",
            },
            {
                "action": "save_candidates_log",
                "target_count_from_config": "target_count",
            },
            {"action": "pick_image", "save_to": "best_idx"},
            {
                "action": "click_image",
                "from_var": "best_idx",
                "masonry_from_var": "ready_result.masonry_index",
            },
            {"action": "wait_url_match", "pattern": r".*/imagine/post/"},
            {"action": "human_pause", "min_ms": 600, "max_ms": 1200},
            {"action": "download_to", "from_config": "output_path"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1000},
            {"action": "click_back"},
            {"action": "wait_url_match", "pattern": r".*/imagine(?!/post)"},
        ],
    },

    "image_to_image": {
        "name": "Image-to-Image",
        "loop_per_prompt": True,
        "steps": [
            {"action": "ensure_at", "url": "/imagine"},
            {"action": "set_mode", "value": "image"},
            {"action": "set_quality", "from_config": "quality"},
            {"action": "set_aspect", "from_config": "aspect"},
            {"action": "upload_ref_if_present", "from_prompt": "ref"},
            {"action": "fill_prompt", "from_prompt": "text"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1200},
            {
                "action": "submit_and_wait_ready",
                "target_count_from_config": "target_count",
                "save_to": "ready_result",
            },
            {
                "action": "save_candidates_log",
                "target_count_from_config": "target_count",
            },
            {"action": "pick_image", "save_to": "best_idx"},
            {
                "action": "click_image",
                "from_var": "best_idx",
                "masonry_from_var": "ready_result.masonry_index",
            },
            {"action": "wait_url_match", "pattern": r".*/imagine/post/"},
            {"action": "human_pause", "min_ms": 600, "max_ms": 1200},
            {"action": "download_to", "from_config": "output_path"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1000},
            {"action": "click_back"},
            {"action": "wait_url_match", "pattern": r".*/imagine(?!/post)"},
        ],
    },

    "text_to_video": {
        "name": "Text-to-Video",
        "loop_per_prompt": True,
        "steps": [
            {"action": "ensure_at", "url": "/imagine"},
            {"action": "set_mode", "value": "video"},
            {"action": "set_video_resolution", "from_config": "resolution"},
            {"action": "set_video_duration", "from_config": "duration"},
            {"action": "set_aspect", "from_config": "aspect"},
            {"action": "fill_prompt", "from_prompt": "text"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1200},
            {"action": "click_submit"},
            {"action": "wait_url_match", "pattern": r".*/imagine/post/"},
            {"action": "wait_video_ready"},
            {"action": "human_pause", "min_ms": 600, "max_ms": 1200},
            {"action": "download_to", "from_config": "output_path"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1000},
            {"action": "click_back"},
            {"action": "wait_url_match", "pattern": r".*/imagine(?!/post)"},
        ],
    },

    "image_to_video": {
        "name": "Image-to-Video",
        "loop_per_prompt": True,
        "steps": [
            {"action": "ensure_at", "url": "/imagine"},
            {"action": "set_mode", "value": "video"},
            {"action": "set_video_resolution", "from_config": "resolution"},
            {"action": "set_video_duration", "from_config": "duration"},
            {"action": "set_aspect", "from_config": "aspect"},
            {"action": "upload_ref_if_present", "from_prompt": "ref"},
            {"action": "fill_prompt", "from_prompt": "text"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1200},
            {"action": "click_submit"},
            {"action": "wait_url_match", "pattern": r".*/imagine/post/"},
            {"action": "wait_video_ready"},
            {"action": "human_pause", "min_ms": 600, "max_ms": 1200},
            {"action": "download_to", "from_config": "output_path"},
            {"action": "human_pause", "min_ms": 500, "max_ms": 1000},
            {"action": "click_back"},
            {"action": "wait_url_match", "pattern": r".*/imagine(?!/post)"},
        ],
    },
}
