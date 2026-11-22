import unittest
from photo_backer_upper.reconcile import compare, same_photo, candidate_stems, _fraction


def photo(**kw):
    base = {
        "date_original": "2013-10-21T19:23:56.854000-06:00",
        "ismovie": False,
        "exif_info": {"camera_model": "iPhone 5s"},
        "original_width": 2049,
        "original_height": 1537,
    }
    base.update(kw)
    return base


def meta(**kw):
    base = {
        "SubSecDateTimeOriginal": "2013:10:21 19:23:56.854",
        "Model": "iPhone 5s",
        "ImageWidth": 3264,
        "ImageHeight": 2448,
    }
    base.update(kw)
    return base


class SamePhotoTests(unittest.TestCase):
    def test_same_photo_downscaled_in_library(self):
        self.assertTrue(same_photo(photo(), meta()))

    def test_rotation_does_not_matter(self):
        self.assertTrue(same_photo(photo(original_width=1537, original_height=2049), meta()))

    def test_different_camera_is_a_collision(self):
        self.assertFalse(same_photo(photo(), meta(Model="Pixel 7")))

    def test_different_subsecond_is_a_collision(self):
        self.assertFalse(
            same_photo(photo(), meta(SubSecDateTimeOriginal="2013:10:21 19:23:56.120"))
        )

    def test_cropped_edit_tolerated_when_camera_and_time_agree(self):
        self.assertTrue(same_photo(photo(original_width=1000, original_height=1000), meta()))

    def test_aspect_mismatch_alone_is_not_enough(self):
        p = photo(
            exif_info={},
            date_original="2020-01-01T10:00:00-07:00",
            original_width=1000,
            original_height=1000,
        )
        self.assertFalse(same_photo(p, {"ImageWidth": 3264, "ImageHeight": 2448}))

    def test_aspect_only_match_for_screenshots(self):
        p = photo(
            exif_info={},
            date_original="2020-01-01T10:00:00-07:00",
            original_width=1170,
            original_height=2532,
        )
        self.assertTrue(same_photo(p, {"ImageWidth": 1170, "ImageHeight": 2532}))

    def test_nothing_to_compare_is_not_confirmed(self):
        p = {"date_original": "2005-01-01T10:00:00-07:00", "ismovie": False}
        self.assertFalse(same_photo(p, {}))

    def test_video_duration(self):
        v = {
            "date_original": "2020-01-01T10:00:00-07:00",
            "ismovie": True,
            "duration": 30.0,
            "original_width": 1920,
            "original_height": 1080,
        }
        self.assertTrue(same_photo(v, {"Duration": 30.4, "ImageWidth": 1920, "ImageHeight": 1080}))
        self.assertFalse(same_photo(v, {"Duration": 95.0, "ImageWidth": 1920, "ImageHeight": 1080}))

    def test_zero_subsecond_on_both_is_ignored(self):
        p = photo(date_original="2020-01-01T10:00:00.000000-07:00", exif_info={})
        agree, differ = compare(p, {"SubSecDateTimeOriginal": "2020:01:01 10:00:00.00"})
        self.assertNotIn("subsec", agree | differ)

    def test_fraction_and_stems(self):
        self.assertEqual(_fraction("2015:03:12 11:26:33.441-06:00"), "44")
        self.assertEqual(
            candidate_stems(
                {"date_original": "2013-10-21T19:23:56-06:00", "date": "2013-10-21T19:24:10-06:00"}
            ),
            ["2013-10-21_19-23-56", "2013-10-21_19-24-10"],
        )


if __name__ == "__main__":
    unittest.main()
