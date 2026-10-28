"""Collecte quotidienne des créations et reprises (BODACC) dans le Nord (59).

Chaque matin de semaine :
1. récupère les annonces BODACC (créations et ventes de fonds) des 30 derniers jours ;
2. garde celles qui correspondent aux cibles (restaurants, artisans, commerces) ;
3. enrichit les nouvelles avec l'annuaire des entreprises (code NAF, enseigne) ;
4. publie data/lot-du-jour.json : 25 candidats jamais attribués, les plus récents d'abord.

Seules des informations professionnelles sont publiées : jamais d'adresse ni de nom
de personne physique. Le fichier data/attribues.txt mémorise les SIREN déjà attribués
pour qu'un même établissement ne revienne jamais deux fois.
Lu ensuite par la tâche planifiée Claude « Veille prospects Nord ».
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta, datetime, timezone

DEPT = "59"
JOURS = 30
TAILLE_LOT = 25
OUBLI_JOURS = 120  # les SIREN attribués sont oubliés après ce délai
BODACC = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records"
RECHERCHE = "https://recherche-entreprises.api.gouv.fr/search"
UA = {"User-Agent": "veille-prospects-luden/1.0"}
ATTRIBUES = "data/attribues.txt"

CIBLE = re.compile(
    r"restaura|brasserie|pizz|snack|friterie|kebab|sandwich|salon de th|traiteur|"
    r"boulang|p[âa]tiss|boucher|charcut|fromag|primeur|[ée]picerie|caviste|vins|"
    r"fleur|coiff|barbier|esth[ée]ti|onglerie|institut de beaut|tatou|toilettage|"
    r"plomb|[ée]lectricit|[ée]lectricien|couvr|toiture|menuis|peintur|peintre|ma[çc]on|"
    r"carrel|chauffag|serrur|garage|r[ée]paration (de v[ée]hicules|automobile)|carrosserie|"
    r"opticien|bijout|cordonn|v[êe]tement|chaussure|boutique|d[ée]bit de boissons|caf[ée]|bar ",
    re.I)
EXCLU = re.compile(
    r"livraison (de|des) (plats|repas|colis)|VTC|transport|holding|conseil|"
    r"location (de )?(biens|immeubles|logements|meubl)|marchand de biens|informatique|"
    r"nettoyage|portage|int[ée]rim|formation|apporteur d.affaire|agent commercial|"
    r"(vente|commerce)[^.]{0,40}(en ligne|à distance|internet|e-commerce)|commerce [ée]lectronique",
    re.I)


def get_json(url, params, essais=3, delai=60):
    full = url + "?" + urllib.parse.urlencode(params)
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers=UA), timeout=delai) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if i == essais - 1:
                raise
            print(f"Nouvel essai après erreur : {e}")
            time.sleep(5 * (i + 1))


def champ_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


def etablissement_de(annonce):
    e = champ_json(annonce.get("listeetablissements"))
    if isinstance(e, dict):
        e = e.get("etablissement", e)
    if isinstance(e, list):
        e = e[0] if e else {}
    return e if isinstance(e, dict) else {}


def siren_de(annonce):
    for r in annonce.get("registre") or []:
        chiffres = "".join(c for c in str(r) if c.isdigit())
        if len(chiffres) == 9:
            return chiffres
    return None


def annonces_bodacc():
    depuis = (date.today() - timedelta(days=JOURS)).isoformat()
    where = (f'numerodepartement="{DEPT}" and familleavis in ("creation","vente") '
             f'and dateparution>="{depuis}"')
    tout, offset = [], 0
    while True:
        page = get_json(BODACC, {"where": where, "order_by": "dateparution desc",
                                 "limit": 100, "offset": offset})
        res = page.get("results", [])
        tout.extend(res)
        print(f"BODACC : page {offset // 100 + 1}, {len(tout)}/{page.get('total_count', '?')} annonces")
        offset += 100
        if not res or offset >= page.get("total_count", 0) or offset >= 9900:
            break
    return tout


def enrichir(siren):
    try:
        data = get_json(RECHERCHE, {"q": siren, "per_page": 1}, essais=1, delai=15)
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    res = data.get("results") or []
    if not res:
        return {}, None
    e = res[0]
    siege = e.get("siege") or {}
    return {
        "nature_juridique": e.get("nature_juridique"),
        "nom_complet": e.get("nom_complet"),
        "naf": e.get("activite_principale"),
        "enseignes": siege.get("liste_enseignes") or [],
        "etat": e.get("etat_administratif"),
    }, None


def lire_attribues():
    vus = {}
    try:
        for ligne in open(ATTRIBUES, encoding="utf-8"):
            parts = ligne.split()
            if len(parts) >= 2 and not ligne.startswith("#"):
                vus[parts[0]] = parts[1]
    except FileNotFoundError:
        pass
    return vus


def ecrire_attribues(vus):
    limite = (date.today() - timedelta(days=OUBLI_JOURS)).isoformat()
    with open(ATTRIBUES, "w", encoding="utf-8") as f:
        f.write("# SIREN déjà attribués à un lot du jour (SIREN date), oubliés après "
                f"{OUBLI_JOURS} jours\n")
        for siren, jour in sorted(vus.items(), key=lambda kv: kv[1]):
            if jour >= limite:
                f.write(f"{siren} {jour}\n")


def candidat(annonce, aujourd_hui):
    """Construit la fiche publique d'une annonce ciblée, ou None si hors cible."""
    etab = etablissement_de(annonce)
    activite = etab.get("activite") or ""
    if not CIBLE.search(activite) or EXCLU.search(activite):
        return None
    siren = siren_de(annonce)
    if not siren:
        return None
    acte = champ_json(annonce.get("acte"))
    ref = (acte.get("dateCommencementActivite") or acte.get("dateImmatriculation")
           or annonce.get("dateparution"))
    try:
        ecart = (date.fromisoformat(ref[:10]) - aujourd_hui).days
    except (TypeError, ValueError):
        return None
    if not -JOURS <= ecart <= JOURS:
        return None
    categorie = json.dumps(acte, ensure_ascii=False).lower()
    commercant = annonce.get("commercant") or ""
    personne_physique = "personne physique" in categorie or "," in commercant
    enseigne = etab.get("enseigne") or etab.get("nomCommercial")
    return {
        "siren": siren,
        "type": "reprise" if annonce.get("familleavis") == "vente" else "creation",
        "date_reference": ref[:10],
        "ville": annonce.get("ville"),
        "cp": annonce.get("cp"),
        "activite": activite[:300],
        "enseigne": enseigne,
        # le nom n'est publié que pour les sociétés, jamais pour une personne physique
        "nom_societe": None if personne_physique else commercant or None,
        "entreprise_individuelle": personne_physique,
        "annonce_bodacc": annonce.get("url_complete"),
    }


DOMICILE = re.compile(r"\b(apt|appartement|app\.?|r[ée]sidence|b[âa]t(iment)?|[ée]tage|logement)\b", re.I)


def adresse_de(etab):
    a = etab.get("adresse") or {}
    if not isinstance(a, dict):
        return ""
    morceaux = [a.get("numeroVoie"), a.get("indiceRepetition"), a.get("typeVoie"), a.get("nomVoie"),
                a.get("complGeographique"), a.get("codePostal"), a.get("ville")]
    return " ".join(str(m) for m in morceaux if m)


def dirigeants_de(annonce):
    lp = champ_json(annonce.get("listepersonnes"))
    if isinstance(lp, dict):
        lp = lp.get("personne", lp)
    personnes = lp if isinstance(lp, list) else [lp]
    noms = []
    for pers in personnes:
        if not isinstance(pers, dict):
            continue
        if pers.get("administration"):
            noms.append(str(pers["administration"])[:300])
        elif pers.get("nom"):
            prenom = pers.get("prenom") or ""
            noms.append(f"{prenom} {pers['nom']}".strip())
    return noms


def details_de(annonce, fiche, info):
    """Coordonnées professionnelles complètes, publiées uniquement sous forme chiffrée."""
    etab = etablissement_de(annonce)
    adresse = adresse_de(etab)
    return {
        "siren": fiche["siren"],
        "denomination": annonce.get("commercant"),
        "nom_complet_annuaire": (info or {}).get("nom_complet"),
        "enseigne": fiche.get("enseigne"),
        "dirigeants": dirigeants_de(annonce),
        "adresse_etablissement": adresse,
        "adresse_probablement_domicile": bool(DOMICILE.search(adresse)),
        "annonce_bodacc": annonce.get("url_complete"),
    }


def main():
    aujourd_hui = date.today()
    brutes = annonces_bodacc()
    print(f"{len(brutes)} annonces BODACC récupérées")
    vus = lire_attribues()
    fiches, sources = {}, {}
    for a in brutes:
        c = candidat(a, aujourd_hui)
        if c and c["siren"] not in vus and c["siren"] not in fiches:
            fiches[c["siren"]] = c
            sources[c["siren"]] = a
    print(f"{len(fiches)} candidats ciblés jamais attribués")

    lot = sorted(fiches.values(), key=lambda c: c["date_reference"], reverse=True)[:TAILLE_LOT]
    echecs, infos = 0, {}
    for c in lot:
        if echecs >= 5:
            break
        info, err = enrichir(c["siren"])
        if err:
            echecs += 1
            continue
        infos[c["siren"]] = info
        if info:
            if "NON-DIFFUSIBLE" in json.dumps(info).upper():
                c["non_diffusible"] = True  # l'exploitant refuse la diffusion : jamais proposé
                continue
            c["naf"] = info["naf"]
            c["etat"] = info["etat"]
            if not c["enseigne"] and info["enseignes"]:
                c["enseigne"] = info["enseignes"][0]
            if not c["entreprise_individuelle"] and not c["nom_societe"]:
                c["nom_societe"] = info["nom_complet"]
        time.sleep(0.2)
    # écarte les entreprises cessées et celles dont l'exploitant refuse la diffusion
    lot = [c for c in lot if c.get("etat") != "C" and not c.get("non_diffusible")]

    jour = aujourd_hui.isoformat()
    for c in lot:
        vus[c["siren"]] = jour
    ecrire_attribues(vus)
    with open("data/lot-du-jour.json", "w", encoding="utf-8") as f:
        json.dump({
            "genere_le": datetime.now(timezone.utc).isoformat(),
            "date_du_lot": jour,
            "departement": DEPT,
            "candidats_restants": len(fiches) - len(lot),
            "nombre": len(lot),
            "candidats": lot,
        }, f, ensure_ascii=False, indent=1)
    print(f"Lot du jour : {len(lot)} candidats ({len(fiches) - len(lot)} en réserve)")

    # détails complets, jamais écrits dans le dépôt en clair : le workflow les chiffre
    chemin = os.environ.get("DETAILS_PATH", "/tmp/details-du-jour.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({"date_du_lot": jour,
                   "details": [details_de(sources[c["siren"]], c, infos.get(c["siren"])) for c in lot]},
                  f, ensure_ascii=False, indent=1)
    print(f"Détails écrits dans {chemin}")


if __name__ == "__main__":
    import traceback
    try:
        main()
        open("data/statut.txt", "w", encoding="utf-8").write(
            f"OK {datetime.now(timezone.utc).isoformat()}\n")
    except Exception:  # noqa: BLE001
        open("data/statut.txt", "w", encoding="utf-8").write(
            f"ERREUR {datetime.now(timezone.utc).isoformat()}\n" + traceback.format_exc())
        raise
