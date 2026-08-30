"""Everything that knows about Garmin.

This is the ONLY package permitted to import `garminconnect` (docs/SCOPE.md §11).
The tool layer talks to these functions, never to a `Garmin` object, so replacing
the data source later touches this package alone.
"""
