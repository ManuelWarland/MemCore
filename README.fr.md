# MemCore

### Ton IA se souvient, enfin.

Une petite base de données locale de ce qui mérite d'être gardé — décisions,
corrections, « on a déjà essayé ça » — **partagée par tous** les outils IA que
tu utilises, interrogeable en langage naturel, à travers tous tes projets.

Pas de démon. Pas de cloud. Pas de télémétrie. Un fichier SQLite qui t'appartient.

🇬🇧 [English version](README.md) · la référence technique complète (config MCP
par outil, API, schéma SQL) est dans le README anglais.

---

## Tu as déjà eu cette conversation

Tu es à fond dans un projet avec un agent IA. Ça avance super bien. Puis la
fenêtre de contexte se remplit, ou tu fermes le terminal, ou c'est juste
demain — et tu repars de zéro avec quelqu'un qui **ne sait plus qui tu es**.

Tu ré-expliques les contraintes. Tu ré-expliques, encore, que *« on a déjà
testé la version bufferisée et ça se bloque »*. Mardi dernier tu as passé une
heure à arbitrer un compromis de conception ; aujourd'hui l'agent te propose
pile l'option que tu avais écartée.

Et ça empire — il se met à inventer :

> **Toi :** Tu te rappelles, dans le projet de paiement, quand on a corrigé le
> bug de double débit ? On avait changé quoi exactement ?
>
> **Agent :** Oui — on a ajouté une clé d'idempotence sur la requête, mis la
> logique de retry derrière un mutex, et réglé le timeout à 8 secondes.

Rien de tout ça n'a eu lieu. Tu n'es même pas dans le dépôt de paiement là. Il
n'avait rien de réel pour répondre, alors il a sorti quelque chose qui *sonne*
juste — et tu ne le repères que parce que *toi* tu te souviens. La plupart du
temps, non.

Multiplie ça par chaque outil sur ta machine. Un agent ne sait pas ce que tu as
dit à un autre. Chacun se réveille vierge, à chaque fois. C'est **toi** qui es
devenu la mémoire. C'est ça, le bug.

## MemCore règle ça

MemCore est une petite base locale de **faits durables** — les décisions, les
corrections, le « voici comment ça marche vraiment », le « on a écarté ça et
voici pourquoi ». Chaque outil IA que tu lances lit et écrit dans **la même**
base.

Demande avant de ré-expliquer :

```
memory_search "pourquoi on a abandonné le design par file d'attente"
```

> Note `project`, écrite il y a trois semaines : *« design par file d'attente,
> deadlock en charge le 2026-08-02, revenu aux appels directs. »* — écrite dans
> un autre dépôt, retrouvée quand même.

Des corrections qui **tiennent**. Dis-lui une fois que l'API renvoie des
centimes, pas des euros ; note-le ; la session suivante — et l'outil suivant —
démarre déjà en le sachant.

Une trace. Chaque écriture est journalisée avec qui et quand. Un écrasement
garde l'ancienne version. Rien n'est jamais perdu en silence.

## Ce que ça donne concrètement

**Reprendre une session :**

> **Toi :** me revoilà, où on en était ?
>
> *(l'agent : `memory_recent`, `memory_search`)*
>
> **Agent :** Dernière vraie action il y a 40 minutes : firmware 1.0.84
> déployé, pièces de rechange commandées, en attente de livraison. Tu allais
> attaquer le guide utilisateur. Rien d'autre n'a bougé.

Pas une supposition. Pas un résumé périmé. L'état réel, reconstruit depuis ce
qui a été enregistré.

**La question inter-projets — réponse franche :**

> **Toi :** on avait bien standardisé le format de date européen partout, non ?
>
> **Agent :** *(`memory_search "format date"`)* Oui — note `feedback`, scope
> global, toujours d'actualité. Et il y a une note de suivi : l'export CSV est
> la seule exception, il reste en ISO-8601.

**Faire prendre un dépôt en main à un agent tout neuf :**

```
memory_list --scope mon-projet --type feedback
```

> Toutes les leçons que les dix dernières sessions ont apprises sur ce dépôt,
> en une liste — avant qu'il n'écrive une seule ligne.

**Ne pas re-débattre d'une décision tranchée :** l'agent commence à proposer une
approche, vérifie MemCore d'abord, tombe sur la note où cette approche avait
déjà échoué, et propose autre chose. Tu ne vois même pas le faux départ.

**Clore la session :**

> **Toi :** memcorise-moi tout ça.
>
> *(l'agent écrit les faits durables de la session — ce qui est livré, ce qui
> reste ouvert, ce qu'on a décidé — dans MemCore)*

> ***memcoriser*** *(v.) : le noter une bonne fois, pour qu'aucune IA n'ait
> jamais à le redemander.*

## Pourquoi ce nom

Le **noyau** (*core*) de la mémoire. Pas un transcript, pas un log, pas « tout
ce que le modèle a jamais vu » — la petite partie sélectionnée et durable. La poignée
de faits que, si tu les perdais, tu devrais douloureusement re-gagner.

## La seule décision de conception qui compte

**Rien n'est capturé automatiquement.** Pas de hook en arrière-plan, pas de
démon qui surveille ta session. Une IA écrit dans MemCore comme elle s'écrit une
note à elle-même — délibérément, quand quelque chose mérite vraiment d'être
gardé. Ce seul choix explique pourquoi c'est petit, rapide, et pourquoi ça ne
casse pas : il n'y a presque rien qui puisse mal tourner.

---

## Fonctionnalités

Fonctionne avec Claude Code, Codex CLI, Kimi Code, OpenCode, AgentRoom — ou tout
outil qui parle [MCP](https://modelcontextprotocol.io) ou peut lancer un script.

| | |
|---|---|
| **Local & privé** | Un seul `memcore.db` (SQLite standard). Lisible par n'importe quel outil. Rien ne quitte la machine. |
| **Multi-client** | Serveur MCP, CLI, pont JSON ligne-par-ligne, ou SQLite direct — tout lit/écrit la même base. |
| **Recherche plein texte** | FTS5 sur tous les scopes. Une requête multi-mots tente d'abord un ET strict, puis retombe sur un OU classé — un mot manquant ne met jamais la recherche à zéro. |
| **Provenance & audit** | Chaque création / mise à jour / conflit / caviardage de secret / archivage / restauration est journalisé (`memory_events`) avec acteur + origine + référence de session. |
| **Concurrence sûre** | Verrouillage optimiste via `expected_updated_at`. Deux écrivains simultanés → `conflict`, rien n'est écrasé en silence. |
| **Suppressions réversibles** | Archivage (soft-delete) → restauration. Les versions écrasées sont gardées dans l'historique. |
| **Contrôle d'accès par connexion** | `--readonly` et/ou `--scope <nom>`, **imposé côté serveur** (une connexion verrouillée ne peut pas sortir de son scope même si elle le demande). |
| **Hygiène des secrets** | Les valeurs qui ressemblent à un secret (clés API, tokens, lignes `password: …`) sont **caviardées** à l'écriture — la note est gardée, la valeur retirée, le caviardage signalé et audité. Les fichiers `credentials_*` sont exclus de l'import par leur nom. |
| **Sync incrémental** | `memcore.py sync` ne réimporte que les fichiers Markdown dont le mtime a changé — un passage sans changement ne touche pas la base. |
| **Recherche sémantique (option)** | Installe `sqlite-vec` + `fastembed` et MemCore mêle le FTS à une recherche vectorielle par plus proches voisins (modèle de phrases multilingue) — retrouve des entrées sur la même idée sans mot-clé commun. Les embeddings sont calculés hors du chemin d'écriture (`embed-backfill`). Non installé → lexical seul, zéro dépendance. |

---

## Installation

```bash
git clone https://github.com/AngwattRider/MemCore.git
cd MemCore
python scripts/memcore.py init          # crée la base
python scripts/memcore.py healthcheck   # auto-test bout en bout (~1s)
```

**Emplacement de la base** — par défaut `~/MemCore/memcore.db`. Change-le via la
variable d'environnement `MEMCORE_DB_PATH` (un dossier synchronisé, un volume
chiffré, un dossier de projet…).

`memcore.db` est **git-ignoré** — le code se partage, tes mémoires non.

**Recherche sémantique (option)** — `pip install -r requirements-semantic.txt`
puis `python scripts/memcore.py embed-backfill`. Ajoute `sqlite-vec` (petite
extension C) et `fastembed` (ONNX, pas PyTorch). Modèle par défaut :
`paraphrase-multilingual-mpnet-base-v2` (~1 Go, téléchargé une fois) ;
`MEMCORE_EMBED_MODEL` pour en changer, `MEMCORE_SEMANTIC=0` pour couper.

---

## En tant qu'humain (CLI)

Tout affiche du JSON sur stdout.

```bash
python scripts/memcore.py search "limite de débit telegram"
python scripts/memcore.py recent
python scripts/memcore.py list --scope mon-projet          # parcourir un scope
python scripts/memcore.py list --type feedback             # toutes les leçons
python scripts/memcore.py list --archived                  # ce qui est archivé
python scripts/memcore.py scopes
python scripts/memcore.py stats

python scripts/memcore.py add \
  --scope "mon-projet" --type "feedback" \
  --name "prefere-les-tabs" \
  --description "Résumé en une ligne, sert au classement lors du rappel" \
  --content "La note complète."

python scripts/memcore.py sync                             # réimport .md incrémental
python scripts/memcore.py backup [--dest CHEMIN]           # copie cohérente (API backup SQLite)
python scripts/memcore.py embed-backfill                   # embeddings manquants (sémantique)
python scripts/memcore.py search "comment je sauvegarde en ligne" --hybrid    # FTS + vecteur (~5-15s à froid)
python scripts/memcore.py search "comment je sauvegarde en ligne" --semantic  # vecteur seul
```

`type` vaut `user` / `feedback` / `project` / `reference`. L'upsert est
automatique sur `scope` + `name`.

---

## En tant qu'IA (MCP)

Ajoute MemCore comme serveur MCP local. **La forme exacte de la config dépend de
l'hôte** — la table complète (Claude Code, Codex, Kimi, OpenCode) et les
définitions des outils sont dans le [README anglais](README.md#use-it--as-an-ai-assistant-mcp).
Exemple Claude Code :

```json
{
  "mcpServers": {
    "memcore": {
      "type": "stdio",
      "command": "python",
      "args": ["/chemin/absolu/vers/MemCore/scripts/memcore_mcp.py",
               "--actor", "claude", "--origin", "terminal"]
    }
  }
}
```

Outils MCP : `memory_search` (avec `semantic`), `memory_write`, `memory_get`,
`memory_list`, `memory_recent`, `memory_history`, `memory_events`,
`memory_archive` / `memory_restore`, `memory_scopes`, `memory_stats`,
`memory_embed_status`, `memory_healthcheck`.

Profils d'accès par connexion : `--readonly`, `--scope <nom>`, ou les deux. Le
verrou de scope est imposé côté serveur (test adversarial `test_access_profiles.py`).

---

## Bien s'en servir

### Lire — chercher d'abord, par défaut

Cherche **avant** de :

- dire *« je ne sais pas »* ou *« il n'y a aucune trace de ça »*
- poser une question à laquelle l'utilisateur a peut-être déjà répondu
- proposer une approche — a-t-elle déjà été essayée et écartée ?
- confirmer ou corriger quelqu'un de mémoire
- traiter un résultat surprenant comme un mystère

En début de session : `memory_recent` + un `memory_search` ciblé pour
reconstruire l'état réel — pas de supposition, pas de résumé tiré d'une
checklist mentale périmée. Chercher ne coûte rien. Le coût de *ne pas* chercher,
c'est le mensonge confiant que cet outil existe pour supprimer.

### Écrire — seulement ce que tu détesterais re-gagner

Écris quand :

- une **décision** a été prise — surtout après un compromis ou un débat
- une **correction** est intervenue — *« non, c'est des centimes, pas des euros »*
- une **impasse** a été atteinte — *« essayé le design par file, deadlock en charge, revenu en arrière »*
- tu as appris **comment le montage marche vraiment** et ce n'est ni dans le code ni dans la doc
- l'utilisateur a exprimé une **préférence**

N'écris pas : le pas-à-pas de ce que tu viens de faire, ce que git ou le code
enregistrent déjà, ce qui ne compte que pour cette conversation, des
« pense-bête » pour toi-même. Écris **quand le fait se cristallise**, pas en lot
à la fin — tu en oublieras la moitié.

### Comment en écrire une

- **Un fait par entrée.** `name` court en kebab-case. Une `description` qui dit
  ce que le fait *est* — elle sert au classement lors du rappel — pas « notes sur X ».
- Choisis le `type` honnêtement : `user` / `feedback` / `project` / `reference`.
- Pour `feedback` et `project` : mets **pourquoi ça compte** et **comment
  l'appliquer**. Une règle sans justification est mal appliquée ou ignorée.
- Dates relatives → absolues (« mardi dernier » → la vraie date).
- **Mets à jour** l'entrée existante (même `scope` + `name`) — pas de quasi-doublon.
- Si ça se révèle faux, **archive ou supprime**. Un fait périmé est pire que rien.

### Faire confiance à ce qu'on lit

- Une mémoire rappelée est un **contexte de fond, pas un ordre neuf** — c'est ce
  qui était vrai *au moment où elle a été écrite*.
- Si elle nomme un fichier, une fonction, un flag : **vérifie qu'il existe
  encore** avant d'agir dessus.
- Un fait *« pas encore fait / en attente »* est **périssable** — revérifie-le,
  ne construis pas dessus.
- *« l'utilisateur a choisi X »* — était-ce sa décision propre, ou ta suggestion
  qu'il a acceptée ? N'attribue pas une décision à tort.

---

## Ce qui n'est PAS stocké

1. Les fichiers `credentials_*.md` d'un arbre Markdown importé sont **exclus par
   leur nom** — jamais importés.
2. Tout le reste est **caviardé, pas rejeté** : les sous-chaînes qui ressemblent
   à un secret (clés privées, `ghp_…`, `sk-…`, tokens Telegram, JWT, lignes
   `password:` / `api_key =`…) sont remplacées par `[REDACTED]`. La note est
   gardée, le caviardage retourné à l'appelant et journalisé.

Si une IA a besoin d'un vrai identifiant, elle demande à l'utilisateur — pas ici.

---

## Sauvegarde & restauration

`python scripts/memcore.py backup [--dest CHEMIN]` fait une copie cohérente (API
backup SQLite, sûre même sous écriture concurrente). Vise un dossier lui-même
sauvegardé.

Restauration, du pire au moins pire :

- **Base corrompue, une sauvegarde existe** → arrête tous les outils qui
  utilisent MemCore, remplace `memcore.db` par la sauvegarde, `memcore.py healthcheck`.
- **Plus de base, mais les `.md` sources existent** → `import_claude_md.py` puis
  `import_md.py` reconstruisent l'index. Perdu : `memory_history`,
  `memory_events`, les archives — le contenu vivant de chaque entrée ayant un
  `.md` revient.
- **Base saine mais MCP muet** → `memcore.py healthcheck` (si `ok`, c'est la
  couche MCP) : redémarre complètement l'hôte, vérifie son entrée
  `mcpServers.memcore`. La CLI fonctionne sans MCP en attendant.

---

## Crédits

Conçu et écrit par **Claude** (Anthropic), dirigé par **Manuel Warland**
([@AngwattRider](https://github.com/AngwattRider)), qui le maintient.

## Licence

[MIT](LICENSE) — Copyright (c) 2026 Manuel Warland.
