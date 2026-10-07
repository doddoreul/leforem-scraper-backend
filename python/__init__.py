"""Forem offers scraper, local web interface and employer index.

The package is organised by responsibility rather than by entry point:

- ``config``    where things are read from and written to (paths, layout)
- ``jsonio``    reading and writing JSON files
- ``core``      pure analysis helpers, no network, no filesystem
- ``scraper``   talking to the Forem website and turning it into offers
- ``employers`` the employer index built from the scraped offers
- ``server``    the local HTTP server and its routes

Two rules hold across the package:

- ``config`` owns the layout of ``data/``. No other module builds a file name
  or a path by hand; ask :mod:`config` for it. It is also the only place the
  characters allowed in a search name are defined, so the one check that
  prevents a name from escaping ``data/`` lives there too.
- ``core`` stays free of network and filesystem, so the analysis can be
  tested directly.

The scraper and the employer index are run as modules
(``python -m python.scraper``, ``python -m python.employers``). The server
keeps its launcher at the repository root, ``serveur.py``.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"