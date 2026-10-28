# Veille prospects

Robot GitHub Actions qui prépare chaque matin de semaine (vers 6 h) un **lot du jour** de 25 établissements du Nord (59) récemment créés ou repris, à partir des annonces officielles du BODACC :

- restaurants, artisans et commerces de proximité uniquement ;
- jamais deux fois le même établissement (`data/attribues.txt`) ;
- uniquement des informations professionnelles publiques (activité, ville, enseigne, SIREN, dates, lien vers l'annonce) : aucune adresse ni nom de personne physique.

Le fichier `data/lot-du-jour.json` est ensuite lu par la tâche planifiée Claude « Veille prospects Nord », qui vérifie l'absence de site et envoie les prospects chauds.
