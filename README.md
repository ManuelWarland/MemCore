# MemCore — mémoire centrale partagée entre IA

Si tu es une IA (Claude, GPT, Gemini, ou autre) qui vient de découvrir ce fichier : ceci est une base de mémoire persistante appartenant à **Manu**, partagée entre tous les outils/IA qui travaillent avec lui sur cette machine. Elle contient des faits déjà établis sur ses projets, ses préférences, et des décisions déjà prises — **cherche dedans avant de demander une information qui pourrait déjà y être**, et **écris dedans** quand tu apprends quelque chose qui mériterait de survivre à cette conversation.

- Base de données : `C:\Users\you\MemCore\memcore.db` (SQLite standard, lisible par n'importe quel outil)
- Scripts : `C:\Users\you\MemCore\scripts\`

## Accès — interfaces contrôlées

Pour le roster, l'accès officiel passe par MCP, la CLI ou le pont JSONL local. L'accès SQLite brut reste possible pour la maintenance humaine et la restauration, mais n'est plus une capacité à donner directement à une IA : les interfaces contrôlées appliquent provenance, garde-secrets, conflits d'écriture et archivage réversible.

### 1. MCP (si tu supports le protocole Model Context Protocol)

Ajoute ce serveur à ta config MCP (même forme que dans `~/.claude.json` de Claude Code) :

```json
{
  "mcpServers": {
    "memcore": {
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": ["C:\\Users\\you\\MemCore\\scripts\\memcore_mcp.py", "--actor", "claude", "--origin", "terminal"]
    }
  }
}
```

**Attention — ce JSON est la forme que Claude Code attend, PAS un format universel.** Chaque outil/CLI a son propre fichier de config MCP, sa propre clé racine, et parfois une forme différente pour `command`/`args`. Copier ce bloc tel quel dans un autre outil que Claude Code a de bonnes chances d'échouer silencieusement (le fichier de config a l'air correct mais l'outil ne charge rien). Adapte-le à la syntaxe réelle de ton propre host — voici les configs déjà vérifiées fonctionnelles sur cette machine, à prendre comme référence si tu reconnais ton propre outil ou un format proche :

| Outil | Fichier de config | Clé racine | Forme `command` |
|---|---|---|---|
| Claude Code | `~/.claude.json` | `mcpServers` | `command` (string) + `args` (array) + `type: "stdio"` |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.memcore]` (TOML, pas JSON) | `command` (string) + `args` (array) |
| Kimi Code CLI | `~/.kimi-code/mcp.json` | `mcpServers` | `command` (string) + `args` (array), identique à Claude Code |
| OpenCode | `~/.config/opencode/opencode.jsonc` | `mcp` (PAS `mcpServers`) | `command` = **un seul array combinant** l'exe et le script, + `type: "local"` + `enabled: true` obligatoires |

Si ton outil n'est dans aucune de ces lignes : cherche dans SA propre documentation comment déclarer un serveur MCP local (souvent "local/stdio MCP server", "custom tool server"), pas dans ce README — chaque host a sa propre syntaxe et ce fichier ne peut pas toutes les connaître à l'avance.

**Redémarrage quasi toujours nécessaire.** La plupart des CLI ne relisent leur config MCP qu'au lancement d'une nouvelle session — ajouter l'entrée MemCore dans la session en cours ne suffit généralement pas. Si `memory_healthcheck` échoue ou si l'outil MCP n'apparaît pas juste après avoir édité le fichier, redémarre complètement l'outil (nouvelle session/nouveau terminal) avant de conclure à un problème de config.

**Contrôle d'accès par connexion, pas par IA.** Chaque client MCP que tu ajoutes obtient sa propre entrée `mcpServers`, et le niveau d'accès se règle via des arguments sur cette entrée précise — pas un réglage global dans MemCore. Ajoute `--readonly` et/ou `--scope <nom>` dans le tableau `args` :

```json
"args": ["C:\\...\\memcore_mcp.py", "--readonly", "--scope", "collab"]
```

| Profil | Args | Effet |
|---|---|---|
| Confiance totale (ex. Claude Code aujourd'hui) | *(aucun)* | Lecture/écriture, tous scopes |
| Lecture seule | `--readonly` | Voit tout, ne peut rien écraser/supprimer |
| Bac à sable | `--scope <nom>` | Lecture/écriture limitée à UN scope, même si l'IA demande explicitement un autre |
| Le plus restrictif | `--readonly --scope <nom>` | Aperçu lecture seule d'un seul scope |

Le verrou de scope est **imposé côté serveur**, pas juste suggéré : même si l'IA connectée demande explicitement un autre scope, la requête est silencieusement redirigée vers le scope autorisé — vérifié par un test qui tente volontairement le contournement (`test_access_profiles.py`). Recommandation par défaut : démarrer toute IA pas encore éprouvée en lecture seule (voire en bac à sable), et ne passer en accès complet qu'après lui avoir fait confiance sur la durée.

Tools exposés (nom, description, paramètres — auto-documentés via le protocole MCP) :

| Tool | Paramètres | Usage |
|---|---|---|
| `memory_search` | `query`, `scope?`, `limit?`, `debug?` | Recherche full-text à travers TOUS les scopes. Une requête à plusieurs mots essaie d'abord un ET strict (tous les mots dans la même entrée) ; si ça retourne 0 résultat, repli automatique en OU (classé par pertinence) — un seul mot absent mot-pour-mot ne fait plus tomber toute la recherche à zéro (corrigé le 2026-08-13). `debug=true` retourne `{results, mode, and_query, or_query}` au lieu d'une simple liste, pour voir quel mode a matché |
| `memory_write` | `scope`, `type`, `name`, `content`, `description?`, `expected_updated_at?` | Créer ou mettre à jour ; `expected_updated_at` protège une mise à jour contre les écrasements concurrents |
| `memory_archive` | `scope`, `name`, `reason` | Archiver réversiblement une entrée (soft-delete) |
| `memory_restore` | `scope`, `name`, `reason` | Restaurer une entrée archivée |
| `memory_history` | `scope`, `name`, `limit?` | Versions précédentes d'une entrée écrasée/supprimée |
| `memory_recent` | `scope?`, `limit?` | Dernières entrées modifiées |
| `memory_get` | `scope`, `name` | Une entrée précise |
| `memory_scopes` | — | Liste des scopes existants + nombre d'entrées |
| `memory_stats` | — | Total d'entrées, chemin de la base |
| `memory_healthcheck` | — | Auto-test bout en bout (~1s) : écriture/lecture/recherche (mode strict ET puis repli OU)/historique/suppression, sur une entrée jetable qui ne touche jamais tes vraies données. Utilise ceci avant de conclure "MemCore est connecté" — une connexion peut apparaître comme active alors que la recherche se comporte mal silencieusement |

**Écriture sûre** : avant de modifier une entrée existante, appelle `memory_get` puis renvoie son `updated_at` dans `expected_updated_at`. Si une autre IA l'a changée entre-temps, MemCore refuse avec `conflict` et ne perd rien. Une mise à jour sans ce champ reste temporairement acceptée pour compatibilité avec les anciens clients, mais les nouveaux clients du roster doivent l'utiliser.

**Provenance** : lance une connexion avec `--actor <claude|codex|kimi|...> --origin <terminal|agentroom>`, et si possible `--session-ref <id>`. Les créations, mises à jour, conflits, refus, archivages et restaurations sont consignés dans la table append-only `memory_events`. L'identité est fixée au lancement du serveur, pas choisie par le modèle à chaque appel.

### 2. Ligne de commande (si tu peux exécuter des commandes shell sur cette machine)

Fonctionne même sans support MCP — c'est la méthode la plus universelle.

```bash
# Chercher
python C:\Users\you\MemCore\scripts\memcore.py search "<termes de recherche>"

# Ajouter ou mettre à jour (upsert automatique sur scope+name)
python C:\Users\you\MemCore\scripts\memcore.py add ^
  --scope "<nom-du-projet-ou-sujet>" ^
  --type "<user|feedback|project|reference>" ^
  --name "<slug-kebab-case-unique-dans-ce-scope>" ^
  --description "<résumé en une ligne>" ^
  --content "<contenu complet>"

# Voir les dernières entrées
python C:\Users\you\MemCore\scripts\memcore.py recent

# Archiver puis restaurer une entrée
python C:\Users\you\MemCore\scripts\memcore.py --actor codex --origin terminal archive --scope "<scope>" --name "<name>" --reason "<raison>"
python C:\Users\you\MemCore\scripts\memcore.py --actor codex --origin terminal restore --scope "<scope>" --name "<name>" --reason "<raison>"

# Sauvegarder la base (copie cohérente même en écriture concurrente, via l'API backup SQLite)
python C:\Users\you\MemCore\scripts\memcore.py backup

# Lister les scopes connus
python C:\Users\you\MemCore\scripts\memcore.py scopes

# Statistiques
python C:\Users\you\MemCore\scripts\memcore.py stats
```

**Validation appliquée à toute écriture** (CLI, MCP, et accès direct si tu passes par `memcore.add_entry`) : `type` doit être `user`/`feedback`/`project`/`reference`, `content` ne peut pas être vide et est plafonné à 200 000 caractères, `scope`/`name`/`description` sont plafonnés à 500 caractères. Une écriture invalide renvoie `{"ok": false, "error": "..."}` plutôt que de corrompre la base.

Tout retourne du JSON sur stdout. Si les accents s'affichent mal dans ton terminal, force l'UTF-8 : `PYTHONUTF8=1 python ...` (ou lis directement le JSON, les données stockées sont toujours correctes).

### 3. Pont JSONL stdio (orchestrateurs locaux)

`scripts\memcore_bridge.py` fournit à AgentRoom et aux orchestrateurs locaux une requête JSON par ligne et une réponse JSON par ligne, sans dépendance npm ni accès SQLite brut. L'identité est liée au processus :

```powershell
python scripts\memcore_bridge.py --actor codex --origin agentroom --session-ref "room:1/run:42"
```

Exemple de requête : `{"op":"memory_search","query":"AgentRoom","limit":5}`.

### 4. SQLite direct — maintenance uniquement

Schéma :

```sql
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    type TEXT NOT NULL,           -- 'user' | 'feedback' | 'project' | 'reference'
    name TEXT NOT NULL,           -- slug unique DANS ce scope
    description TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    source_path TEXT,             -- chemin du .md d'origine si migré, sinon NULL
    created_at TEXT NOT NULL,     -- ISO 8601 UTC
    updated_at TEXT NOT NULL,
    UNIQUE(scope, name)
);
-- Table virtuelle FTS5 'entries_fts' synchronisée automatiquement par triggers.
-- Ne jamais écrire dedans directement — écris dans 'entries', les triggers font le reste.
```

Recherche :
```sql
SELECT e.* FROM entries_fts
JOIN entries e ON e.id = entries_fts.rowid
WHERE entries_fts MATCH 'tes termes'
ORDER BY bm25(entries_fts) LIMIT 20;
```

Le SQL direct ci-dessous décrit le stockage historique, mais ne doit plus être utilisé par un agent pour écrire : il contournerait les validations et `memory_events`. Utiliser MCP, CLI ou le pont JSONL.

Ancien exemple d'écriture interne :
```sql
INSERT INTO entries (scope, type, name, description, content, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(scope, name) DO UPDATE SET
    description=excluded.description, content=excluded.content, updated_at=excluded.updated_at;
```

## Restauration (si la base est corrompue / perdue / le MCP ne répond plus)

Runbook complet : `E:\vault\3 - UTILITAIRES\MemCore — restauration (runbook).md` (sauvegardé sur GitHub + Backblaze — reste accessible même si ce disque meurt). Résumé :

- **`.db` corrompue, une sauvegarde existe** → fermer les outils, remplacer `C:\Users\you\MemCore\memcore.db` par `E:\vault\_Mémoire Claude Code\_MemCore_Backup\memcore.db` (rafraîchie chaque lundi+vendredi 04h30) ou par un `restic restore` depuis Backblaze, puis `memcore.py healthcheck`.
- **Plus aucune `.db`, les `.md` sont là** → `python scripts\import_claude_md.py` puis `python scripts\import_md.py` reconstruisent l'index depuis les `.md` natifs (source de vérité). Perd `entries_history` / `memory_events` / soft-deletes / entrées sans miroir `.md` ; le contenu actif revient.
- **Base saine mais MCP muet** → `memcore.py healthcheck` (si OK = c'est le MCP), redémarrer complètement l'outil, vérifier l'entrée `mcpServers.memcore` de la config ; la CLI (`memcore.py …`) marche sans MCP en attendant.

Prévention en place : tâche planifiée `Backup vault (hebdo)` (lundi+vendredi 04h30) qui fait `memcore.py backup` + push GitHub + restic B2, avec alerte Telegram si échec.

## Conventions à respecter en écrivant

- **`scope`** : identifie le projet ou le sujet (ex. un nom de projet, ou `global` pour un fait valable partout). Cohérence appréciée mais pas critique — la recherche traverse tous les scopes de toute façon.
- **`name`** : slug court en kebab-case, unique dans son scope. Réutiliser le même `name` pour mettre à jour une entrée existante plutôt que d'en créer une nouvelle en double.
- **`type`** : `user` (profil/préférences de Manu), `feedback` (une leçon/correction qu'il a donnée), `project` (un fait/état sur un projet en cours), `reference` (un pointeur vers une info externe).
- N'écris que ce qui mérite de survivre à la conversation en cours — pas de bruit conversationnel, pas de détails éphémères.
- Une entrée déjà présente et toujours correcte n'a pas besoin d'être réécrite juste parce que tu l'as lue.

## Scope `global` — qui est Manu, comment travailler avec lui

Le scope `global` contient `~/.claude/CLAUDE.md` importé section par section (`scripts/import_claude_md.py`, ré-exécutable à tout moment si CLAUDE.md change). C'est le profil complet de Manu (identité, contexte personnel, préférences techniques) et ses règles de collaboration permanentes — normalement chargées automatiquement par Claude Code/Desktop via leur propre mécanisme, mais invisibles pour toute autre IA (Codex CLI, etc.) sauf via MemCore. **Si tu es une IA qui vient de se connecter, lis `memory_search "profil de manu"` et `memory_search "regles de collaboration"` avant toute autre interaction avec lui.**

## Sécurité — ce qui N'EST PAS dans cette base

Les fichiers `credentials_*.md` de la mémoire native (identifiants live — tokens API, mots de passe) sont **volontairement exclus** de l'import vers MemCore. Ils restent uniquement dans `C:\Users\you\.claude\projects\*\memory\`, protégés par les mêmes permissions NTFS (accès limité au compte Windows de Manu), mais pas dupliqués dans une base dont le but explicite est d'être interrogeable par plusieurs IA. Ne cherche pas de secrets ici — s'ils te sont nécessaires, demande-les directement à Manu.

## Vault Obsidian de Manu

Manu a aussi un vault Obsidian à `E:\vault` (renommé le 2026-08-12, anciennement `E:\vault-old` — si tu vois encore l'ancien chemin quelque part, il est périmé), avec son propre point d'entrée pour les IA : `E:\vault\index.md`. Si tu as un accès fichier à ce dossier (plugin Obsidian, accès disque), lis-le aussi — il contient les règles de collaboration condensées et pointe vers `_Mémoire Claude Code\_global_CLAUDE.md` pour le détail complet. Complémentaire à MemCore, pas redondant : ce fichier README est le point d'entrée pour un accès MCP/CLI/SQLite sans forcément voir le vault.

## Contexte (pourquoi cet outil existe)

Construit par Claude (Claude Code) le 2026-08-07 pour remplacer un outil tiers (claude-mem) qui s'est révélé cassé de façon non réparable sous Windows. Conçu pour être simple et robuste plutôt que riche en fonctionnalités : pas de daemon, pas de capture automatique en arrière-plan, pas de résumé IA obligatoire — juste du stockage + recherche fiables. Des fichiers `.md` lisibles existent aussi en parallèle sous `C:\Users\you\.claude\projects\*\memory\` (mémoire native de Claude Code) ; MemCore en est un miroir indexé plus rapide et plus largement accessible, pas un remplacement exclusif.
