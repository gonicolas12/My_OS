"""Contrôle de l'ordinateur : voir l'écran, cliquer, taper, ouvrir des applications.

Organisation :

* :mod:`tools.computer.screen` — modèle de données (fenêtres, éléments numérotés,
  observation) et texte envoyé au modèle ;
* :mod:`tools.computer.accessibility` — arbre AT-SPI (source principale) ;
* :mod:`tools.computer.ocr` — texte lu par Tesseract (complément) ;
* :mod:`tools.computer.marks` — capture annotée pour un modèle qui voit les images ;
* :mod:`tools.computer.x11` — fenêtres, capture et injection (X11) ;
* :mod:`tools.computer.apps` — applications installées (``open_app``) ;
* :mod:`tools.computer.backend` — assemblage (observer / agir) ;
* :mod:`tools.computer.tools` — les outils exposés au modèle.
"""
