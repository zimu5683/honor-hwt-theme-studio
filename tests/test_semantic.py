from __future__ import annotations

import unittest

from hwtstudio.semantic import SIMPLE_SETTINGS, resolve_all, setting_visible
from hwtstudio.services.catalog_service import load_preferred_catalog


class SemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, _ = load_preferred_catalog()

    def test_thirty_settings_resolve_without_overlap(self):
        resolved = resolve_all(self.catalog)
        self.assertEqual(len(SIMPLE_SETTINGS), 30)
        self.assertEqual(set(resolved), {item.id for item in SIMPLE_SETTINGS})
        seen = set()
        for setting in SIMPLE_SETTINGS:
            slots = resolved[setting.id]
            self.assertTrue(slots, setting.id)
            expected = {"image", "wallpaper", "preview"} if setting.kind == "image" else {setting.kind}
            self.assertTrue(all(slot.resource_type in expected for slot in slots), setting.id)
            ids = {slot.id for slot in slots}
            self.assertFalse(seen & ids, setting.id)
            seen.update(ids)

    def test_phone_profile_hides_only_application_settings(self):
        by_id = {item.id: item for item in SIMPLE_SETTINGS}
        packages = {"com.android.settings"}
        self.assertTrue(setting_visible(by_id["settings_background"], packages))
        self.assertFalse(setting_visible(by_id["messages_background"], packages))
        self.assertFalse(setting_visible(by_id["wechat_brand"], packages))
        self.assertTrue(setting_visible(by_id["page_background"], packages))
        self.assertTrue(setting_visible(by_id["wechat_brand"], None))


if __name__ == "__main__":
    unittest.main()
