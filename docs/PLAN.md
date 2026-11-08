# Hausfunk — Implementation Plan

AI-moderated private home radio with multiple channels. Claude plans each channel's programme and writes the moderation, news and stories; a TTS service voices them; Liquidsoap mixes music and speech into one continuous stream per channel that every device in the house can join live.

## Decisions so far

| Topic | Decision |
|---|---|
| Name | Hausfunk (product). Each channel has its own on-air name, e.g. "Nachtschicht FM". |
| Core concept | **Channel** = name + mount + programme grid + taste source + moderator persona + news topics. People are taste **profiles** that feed channels. |
| Orchestrator | Python 3.13, `uv`, FastAPI, SQLite, Anthropic SDK |
| Music source (v1) | Jellyfin on the network (~37k tracks), streamed via its HTTP API |
| Playout | Liquidsoap, one pipeline per channel → one Icecast mount per channel (continuous mode per channel) |
| Cost control | A channel only generates (LLM, TTS) while it has listeners, based on Icecast listener counts |
| Languages | English and German, configurable per channel and per programme |
| TTS | Provider interface. Dev: Piper (CPU) as placeholder. Target: local model (Chatterbox Multilingual / Voxtral) on the Arc A770 in the Talos cluster. ElevenLabs (smallest tier) for reference and one-off jingles. Fallback: a cheap hosted TTS. |
| Configuration | YAML for structure (channels, profiles, schedules, feeds), every setting overridable via env vars; URLs and secrets env-only (see Phase 0) |
| HomePod / Siri | Existing Home Assistant with HomeKit Bridge: one switch per channel and room, named after the channel; HomeKit's room awareness picks the HomePod that heard the command. OwnTone only if synced multiroom is needed. |
| Local runtime | Podman Compose; Kubernetes (Talos, Argo CD) later |
| Claude models | Configurable per task. Default: Sonnet for planning and scripts, Haiku for bulk work (genre classification). |

## Domain model

| Entity | Fields (main) |
|---|---|
| Profile | person, bans (genre/artist/track), preferred genres with weights, feedback history |
| Channel | name, slug/mount, language, taste source (one profile, a blend of profiles, or genre/format rules only), programme grid, moderator persona (voice, style), news topics, nightly silence window |
| Programme | time range within a channel's grid, genre filters, segment rules (links, news, story), optional persona/language override |
| Persona | role (host, news anchor, storyteller), TTS provider + voice per language, style prompt |

Example lineup:

| Channel | Taste source | Format |
|---|---|---|
| Hausfunk | Household (blend) | Mixed, hourly news |
| Nachtschicht FM | Chris | Electronic, no news, quiet host |
| Klassik am Abend | Genre format | Classical only, short introductions |
| Märchenfunk | Kids profile | Stories and kids' music, German |

## Architecture

```
            Jellyfin (network)          RSS feeds
                  │                         │
                  ▼                         ▼
 ┌────────────────────────── orchestrator (Python) ──────────────────────────┐
 │ library   sync Jellyfin → SQLite index, normalise genres                  │
 │ taste     profiles, bans, preferences, feedback, blending → candidates    │
 │ channels  registry; activates generation only while a channel has         │
 │           listeners                                                        │
 │ schedule  programme grid per channel (genre hours, story time, silence)   │
 │ director  Claude: picks tracks from candidates, writes links/news/stories │
 │ news      fetch, dedupe, cluster, bulletin script (shared across channels)│
 │ tts       provider interface → rendered, loudness-normalised segments     │
 │ playout   keeps each channel's Liquidsoap queue N minutes ahead           │
 │ api       FastAPI: config, like/skip/ban, status, Home Assistant hooks    │
 └────────────────────────────────────┬───────────────────────────────────────┘
                                      │ queue pushes per channel
                                      ▼
            Liquidsoap: one pipeline per channel (crossfade, ducking, normalisation)
                                      │
                                      ▼
          Icecast mounts /hausfunk, /nachtschicht-fm, ...  →  HomePods via Home Assistant
```

Key principles:
- Code filters, Claude chooses. Bans and hard rules are applied before Claude sees anything; Claude only picks from a candidate pool of ~100–300 tracks per block.
- Lookahead. Each active channel's queue and rendered speech stay 1–2 tracks ahead, so a slow API never causes dead air.
- Idle channels cost nothing. When a channel gets its first listener, it starts with a short buffer (e.g. a station ident and the next track) while the planner catches up.
- News items are fetched and summarised once, then voiced per channel language and persona.
- Everything external sits behind an interface: music source, TTS provider, news source.

## Repo layout

```
hausfunk/
  pyproject.toml
  compose.yaml                 # podman compose: icecast, liquidsoap, orchestrator, piper
  .env.example
  config/
    hausfunk.example.yaml      # channels, profiles, personas, schedules, feeds
  liquidsoap/radio.liq         # generated or parameterised per channel
  src/hausfunk/
    library/   taste/   channels/   schedule/   director/   news/   tts/   playout/   api/
    prompts/{en,de}/           # style templates per language and role
  tests/
  docs/PLAN.md
```

## Phases

### Phase 0: Bootstrap
- `uv` project, ruff, pytest, config schema (`pydantic-settings`) covering channels, profiles, personas and programmes, with an example config.
- Config sources, later ones win: built-in defaults → YAML file (`HAUSFUNK_CONFIG` path) → env vars.
  - Prefix `HAUSFUNK_`, nested keys via `__`, e.g. `HAUSFUNK_JELLYFIN__URL`, `HAUSFUNK_TTS__PROVIDER=piper`, `HAUSFUNK_DIRECTOR__MODEL`.
  - Secrets (`HAUSFUNK_JELLYFIN__API_KEY`, `HAUSFUNK_ANTHROPIC__API_KEY`, `HAUSFUNK_ELEVENLABS__API_KEY`, Icecast passwords, Home Assistant token) are never read from the YAML file; they come from env or from a `*_FILE` variant pointing at a mounted secret file (Podman/Kubernetes secrets).
  - All service endpoints are env-configurable: Jellyfin, Icecast, Liquidsoap control, TTS service, Home Assistant, Claude API base URL.
  - `.env.example` documents every variable; startup validates config and fails with a clear message on missing or invalid values (secrets masked in logs).
- `compose.yaml` with Icecast and Liquidsoap containers, both reading their URLs and passwords from the same `.env`.

Done when `podman compose up` starts Icecast and Liquidsoap playing a test file on two mounts.

### Phase 1: Library and taste
- Jellyfin sync into SQLite: artist, album, track, genres, duration, year, play count; incremental resync.
- Genre normalisation: map raw tags to a canonical genre list; classify untagged or ambiguous items with Claude in batches per artist/album, cache the result.
- Profiles: bans (genre/artist/track), preferred genres with weights.
- Taste sources for channels: single profile, blend of profiles (bans of all members apply, preferences merged by weight), or genre/format rules only.
- Candidate pool builder per channel: filters, "recently played on this channel" exclusion window, Jellyfin Instant Mix for similarity.
- Director v1: Claude plans one hour of music for a channel from its candidate pool (structured output: ordered tracks with a short reason).

Done when `hausfunk plan-hour --channel <slug>` prints a sensible, ban-respecting playlist for at least two differently configured channels.

### Phase 2: Playout with moderation
- Liquidsoap: one pipeline per configured channel with its own request queue and Icecast mount; crossfade, loudness normalisation (~-14 to -16 LUFS), ducking music under speech, fallback to silence.
- Orchestrator pushes Jellyfin stream URLs and rendered speech files into each channel's queue (Liquidsoap server/telnet interface; verify against the installed Liquidsoap version).
- Listener-aware generation: poll Icecast listener counts; start planning when a channel gets a listener, stop generating after a grace period with none.
- Moderator links: Claude writes short links between tracks in the channel's language and persona, including the channel name as on-air identity.
- TTS service v1: provider interface with Piper (dev) and ElevenLabs (tests).

Done when two channels stream in parallel for two hours with music and spoken links, no dead air, and an idle channel makes no LLM or TTS calls.

### Phase 2b: Home Assistant and Siri
- Home Assistant exposes one switch per channel and room through the existing HomeKit Bridge, all named after the channel and assigned to their room in the Home app. HomeKit's room awareness makes "Hey Siri, turn on Nachtschicht FM" flip the switch in the room of the HomePod that heard it.
- Automation on: `media_player.play_media` with the channel's Icecast mount on that room's HomePod; off: stop. Switching to another channel in the same room stops the previous one. HomePods appear in HA via the Apple TV integration (pyatv); verify it plays an endless HTTP stream reliably.
- Generate the HA config (switches, automations) from the Hausfunk channel config to avoid hand-maintaining channels × rooms.
- Expose only frequently used channels to HomeKit; the rest via an HA dashboard or the Hausfunk web UI.
- Orchestrator endpoints for HA (`/status`, `/skip`, `/like` per channel) as REST commands or sensors (now playing); HA URL and token env-configurable (`HAUSFUNK_HOMEASSISTANT__URL`, `HAUSFUNK_HOMEASSISTANT__TOKEN`).
- Channel names must not collide with Apple Music; config validation warns on common words, and each name gets a quick "Hey Siri" test.
- If several HomePods must play one channel in sync, add OwnTone (AirPlay 2 sender) instead of per-device pyatv playback.

Done when the same channel-name command starts the right channel on the HomePod in two different rooms.

### Phase 3: News
- Feed config per topic; fetch, dedupe, cluster the same story across sources.
- Claude writes a bulletin in a fixed format: headline, 2–3 detail sentences, short closing remark. Feed language and broadcast language are independent; summaries are produced once and rendered per channel language and persona.
- Inserted at configurable points per channel (e.g. top of the hour), with an intro jingle/bed.

Done when an hourly news break with current items plays on each channel that has news enabled, in that channel's language.

### Phase 4: Programmes and schedule
- Programme grid per channel: time ranges, genre filters, segment rules, optional persona and language override (e.g. a classical hour on the household channel).
- Story time: Claude writes a story from the channel's taste source (length, genre, language), voiced by the storyteller persona.
- Nightly silence per channel.

Done when channels run a full day according to their grids in both languages.

### Phase 5: Feedback and management UI
- API: like, skip, ban, now playing, upcoming, per channel.
- Web UI: manage channels, profiles, personas, programmes, feeds; feedback buttons.
- Skips and likes feed back into the profile(s) behind the channel.

## Parallel spike S1: local TTS on the Arc A770 (risk check, start early)
- Talos: Intel GPU system extension (i915) plus the Intel GPU device plugin; verify current extension names.
- Test pod running Chatterbox Multilingual and Voxtral on PyTorch XPU (fallback: OpenVINO).
- Blind listening test: the same 4–5 texts (news item, link, story excerpt; EN and DE) via ElevenLabs, Chatterbox and Voxtral.
- Check throughput: rendering must keep up with the speech demand of the expected number of simultaneously active channels.
- Outcome decides the production TTS. If the Arc doesn't work out, use a cheap hosted TTS.

## Later
- **Kubernetes:** Argo CD apps, Proxmox CSI PVCs, TTS service on the Arc node.
- **Voice-recognised personal channel:** a personal Siri Shortcut per person ("Hausfunk") that the HomePod runs for the recognised speaker and that starts that person's channel. Open: whether a shortcut can tell which HomePod/room triggered it.
- **Synced multiroom:** OwnTone (AirPlay 2) with `hostNetwork` on a node in the HomePods' L2 network, controlled by Home Assistant.
- **Acquisition:** Discogs (official API) and Beatport (unofficial, optional) for discovery → Claude curation → MusicBrainz match → Lidarr with nuLMD/Tubifarry → Prowlarr → SABnzbd in the cluster, with internet egress via a static IP that the router policy-routes into the VPN tunnel (Cilium Egress Gateway or Multus/macvlan, depending on the CNI).
- **On-demand mode:** a per-listener stream that starts fresh instead of joining the channel's live programme.
- Possibly a native iOS app.

## Open items
- Jellyfin URL and API key; check genre tag quality on a sample.
- Liquidsoap version and its queue control interface; resource use with several pipelines.
- Intel XPU support of the chosen TTS models.
- ElevenLabs credits on the current tier.
- Are the HomePods already media players in Home Assistant, and can HA reach the Icecast stream?
- Does HomeKit's room awareness work reliably with identically named HomeKit Bridge switches?
