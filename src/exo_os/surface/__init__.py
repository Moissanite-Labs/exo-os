"""The desktop surface: a prompt bar, a panel drawer and an approval card (DESIGN.md §7).

`model` turns Exo protocol events into what the person sees and is plain Python, so it is tested
without a display. `conn` talks to exod from a background thread. `app` draws it all with GTK 4 and
gtk4-layer-shell.
"""
