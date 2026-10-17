# Dervo on your phone

First version, 8 Oct 2026. Branch `claude/dervo-iphone-wmgtj8`. Nothing here is deployed or submitted anywhere yet.

## Where things live

Your Mac stays the server. Projects, threads, messages, memory and every run record stay in Dervo's local database in Application Support on the Mac. The phone shows a copy of Home that the Mac sends it, and anything you do on the phone goes back to the Mac and runs through the same host operations the desktop window uses.

```
iPhone app  ──sealed──▶  relay (Cloudflare)  ──sealed──▶  Mac: Dervo window → host
           ◀──sealed──                       ◀──sealed──
```

- **Mac**: the app's own link in Rust (`src/phone`, draft PR #14 from "Make Dervo feel premium") runs in Dervo's process with the window closed and keeps its keys in the Keychain. Phone settings drives it through `frontend/src/local/phoneLinkNative.ts`. On a build without it, the window runs `mac/link.ts` instead (`frontend/src/local/phoneLink.ts`), never both. The TypeScript link is the reference the Rust one is tested against. It reads Home with the state spine's `thread_states` when the host has it (branch b19/state-spine), else `projects` plus `conversations`. It runs a phone's Send as `coding_request` (the phone's action id is the client id, so the host refuses a second copy too) and Stop as `task_stop` on the thread's working tasks.
- **Relay** (`relay/room.ts`, `relay/worker.ts`): one Durable Object per pairing. It forwards sealed envelopes and holds a few for a device that's away: the newest Home snapshot per phone, plus up to 100 queued messages to a phone and 200 to the Mac, each for at most 7 days, until the recipient confirms it got them. A mailbox holds at most 2 MB and a room 8 MB, nothing is held until the Mac has allowed a phone, and the Worker turns away more than 30 connections a minute from one address. Each envelope is stored under its own key, and a Durable Object alarm purges anything past 7 days even for a device that never comes back. It stores a hash of the room key, the devices' public keys, the phone's push token and when the Mac was last connected. It can't read an envelope.
- **Phone** (`../mobile`, Expo): keeps its key pair and pairing in the iOS Keychain (this device only), and the last Home plus pending sends in a file in its caches folder, which isn't in iCloud or device backups.

## Security

- **Pairing:** the Mac shows a QR code with the relay address, a room id and key, the Mac's public key and a one-time code that expires in 10 minutes. The phone sends its own public key with the code, sealed to the Mac. Both screens then show the same six digits, computed from the Mac's key, the phone's key and the code, and nothing is paired until you click Allow on the Mac, so someone who photographs the QR can't quietly take your place. Only then does the Mac tell the relay which phones are allowed. A phone the Mac hasn't allowed can only send the Mac a few live messages a minute (20 a minute across the whole room), and nothing is stored for it.
- **Signing in to the relay:** the relay sends each connection a throwaway key and nonce. The device answers with an HMAC over its role, the room, its key and the challenge, keyed from its secret key and the relay's throwaway key through a labelled hash (`dervo-relay-proof/v2`, spec in `protocol/crypto.ts`). That key is never a box key, so a hostile relay can't turn sign-ins into anything that opens or forges an envelope. Each device answers one challenge per connection, before it's signed in, and never one that names a key in its own pairing. A QR code or a removed phone only carries public keys, so neither can sign in as the Mac or as another phone.
- **Room key:** the Mac and paired phones sign in by key alone. The room key in the QR only lets an unpaired phone in to pair, and the Mac changes it with each new pairing code, so an old QR stops working.
- **Removing a phone:** only a sealed message from the Mac removes a pairing on the phone; a plain relay error never does. A phone removed while it was away learns it the next time it connects: for 7 days the relay lets a removed phone sign in by its key just to collect the Mac's sealed removal, and the Mac sends that notice on its next connect if it was offline at the time. Changing the relay leaves the old room's list as it is, so the sealed removal waits there for a phone that's away.
- **Crowding:** at most 6 connections can be mid sign-in, 4 unpaired phones at once for 60 s each, and 3 connections per paired phone. The Mac always gets in. So a removed phone or an old QR can't lock the Mac out, and rotating the room key isn't needed (it would also cut off the sealed removal).
- **Messages:** NaCl box (X25519 + XSalsa20-Poly1305, tweetnacl). There's a fresh random nonce for each message, and each one is authenticated to its sender's key.
- **Replays:** the Mac remembers every envelope nonce and action id it has handled (`seen`, `done`) for a day longer than an action can live. A Send can wait 24 hours for an away Mac and a Stop only 2 minutes, because a late Stop could hit a different run.
- **What a phone can ask for:** reading Home, reading one thread, sending a message to a thread, stopping a thread's working tasks, and messaging a project's Home coordinator, which can start or route work to threads the same way it does from the Mac. It can also answer an agent's question (a choice, its own words, or skip), which only ever sends words and grants nothing. Approving a run's scope and accepting a finished result (which commits exactly that result's files on the Mac) need a per-phone switch on the Mac, Settings, Phone, off by default; the Mac checks it on every action, and refuses a result that was already decided. A scope that reaches beyond its own files (the whole project, extra folders, full access) is approved only on the Mac, whose sheet shows it. The phone offers Accept only where the Mac's own review sheet would (checks passed or manual review, every file recorded, not a handoff). Nothing else is read (`readPhoneMessage`). A phone can't push, change settings or reach a path.
- **Push:** the relay sends "Something needs you." (a thread started waiting on you) or, more quietly, "Something finished." through Expo's push service, with no thread names or text, at most once a minute per phone, and only when the phone isn't connected.
- **Delivery:** queued envelopes stay on the relay until the recipient confirms them, even when its socket looks live, and the Mac confirms only after it has recorded what it did. Both ends ping every 25 s and reconnect if there's no answer in 10 s. A send made while the phone was offline goes when it reconnects, with the same id, and the Mac never runs it twice. Before running an action the Mac records that it started, so if Dervo closes mid-action it answers "can't tell whether it went through" rather than running it again. A full mailbox drops the sender's own oldest envelopes first, so a chatty phone can't push the Mac's removal or Home out. While a phone is open (it says hello every 50 s), the Mac reads Home every few seconds and re-sends it at least every 30 s, live only. With no phone open it reads every 30 s, just to wake a phone when a thread starts waiting on you. The phone says Home is stale if it hasn't updated for 90 s. A long thread or a very big Home is trimmed to fit one sealed message, newest kept, and says so.
- **Not done yet:**
  - On the Mac, v1 keeps its key pair in the window's storage. Moving it to the Keychain is listed below.
  - There's no forward secrecy: a stolen Mac or phone key could open envelopes the relay still holds, which is at most 7 days of them.
  - An action's age is judged on the Mac's own clock. The phone keeps the newest Mac time it has had sealed from the Mac and stamps each action with the Mac's time at the tap, as it reckons it (`macAt`), so the phone's clock being off doesn't matter and the relay can't change it. The Mac refuses an action without `macAt` or more than 5 minutes ahead of its clock, and keeps its record of each action until the action is too old to run anyway. Stop only touches tasks that were already running at the tap (dated by their last message from you, with 2 s of slack).
  - The desktop window's content security policy reaches only `wss://relay.trydervo.com`. A development relay on the Mac works through the app's own link (PR #14), not through the window's fallback link.

## Why a relay and not Tailscale

| | Relay (chosen) | Tailscale or another direct link |
|---|---|---|
| Works for testers with no setup | Yes | Every user installs and signs in to Tailscale on both devices |
| Push notifications | Yes, from the relay | No, a push still needs a server |
| Phone battery | One WebSocket while open, push while closed | VPN always on |
| iOS limits | None | Takes the phone's one VPN slot |
| Who can read content | Nobody but the two devices | Nobody but the two devices |
| Cost | About $0 to $5 a month for a beta (estimate below) | Free |

Supabase Realtime could relay too, but it's the production accounts database. Adding a message relay there mixes a new risk into it, and it needs production changes. Cloudflare already hosts trydervo.com, and Durable Objects fit one room per pairing.

**Cost (estimated, not measured):** Workers Paid is $5 a month and includes Durable Object requests and duration. v1 keeps the Mac's socket open without hibernation, so a Mac online all day costs about 128 MB × 86,400 s, roughly 11,000 GB-s a day, or about 330,000 GB-s a month. That's within the paid plan's included 400,000 GB-s for one Mac but not for many. Switching the Durable Object to the WebSocket Hibernation API before testers join removes most of that.

## Why Expo (React Native)

- **Shared code:** the phone runs the same TypeScript protocol (`protocol/`) as the Mac link and relay, tested in Node.
- **Feel:** native navigation, gestures, keyboard handling, haptics, the camera and Keychain. Phone Home and Thread had to be new layouts anyway. The desktop's React DOM components don't fit a phone, so Tauri mobile would only have reused tokens while adding a WebView feel.
- **SwiftUI** would be the most native, but nothing would be shared and there'd be a second language to keep in step. Revisit it if the Expo app's feel falls short on device.
- **Rendered review:** the app also runs in a browser, so this container can render and screenshot it (`mobile/scripts/render.ts`).

## Running it

```
cd companion && npm install && npm test          # protocol, relay and Mac link (real Python host)
npm run relay:dev                                # local relay on ws://127.0.0.1:8787
cd ../mobile && npm install && npx expo start    # then i for the iOS Simulator
```

On the Mac, in Dervo, open Preferences, then Phone. Set the relay to `ws://127.0.0.1:8787` and show a code. In the Simulator, paste the code (the Simulator has no camera). A real iPhone needs the deployed relay, because a phone can't reach the Mac's 127.0.0.1.

## Not done (needs Bryce or another lane)

- **Deploy the relay.** Bryce gave the go on 8 Oct 2026. It waits on Cloudflare access only: a `wrangler login` on the Mac, or a `CLOUDFLARE_API_TOKEN` with Workers Scripts edit, Durable Objects and DNS edit on trydervo.com. Then, from `companion/`:

  ```
  npm run relay:deploy                                   # tests, typecheck, then wrangler deploy
  curl https://relay.trydervo.com/health                 # ok
  npm run relay:smoke -- wss://relay.trydervo.com        # pair, allow, Home, removal, end to end
  ```

  `wrangler.toml` serves the Worker on the custom domain `relay.trydervo.com` (Cloudflare adds the DNS record) with no workers.dev URL. Push needs an optional `wrangler secret put EXPO_ACCESS_TOKEN` once the EAS project exists. `npm run relay:check` bundles it without deploying. The smoke run passes against `wrangler dev` (local workerd with real Durable Object storage). Real Cloudflare alarms and rate limiting haven't been checked.
- **Device builds.** These need an EAS project id for push and a development build on Bryce's iPhone (`npx eas-cli build --profile development`), signed with his Apple account. Nothing went to TestFlight.
- **The app's own Mac link** is draft PR #14. Until it's merged, this branch alone runs the window's fallback link, which needs the deployed relay.
- **Home coordinator from the phone** is built on `orchestrator_send` (b19/home-stream, draft PR #13): Message Dervo on Home opens the project's chat with the coordinator. Until #13 lands, mvp1's own `orchestrator_send` takes the message but doesn't queue a second one while it's answering.
- **Answers and approvals on a real host.** Covered by the fake host in `mac/link.test.ts`; the real `task_question_answer`, `task_approve` and `task_review` paths haven't been run from a phone. The app's own Rust link (`src/phone`) doesn't have these actions or the approvals switch yet (spec: `/mnt/project-files/dervo/phone/rust-link-spec-approvals-20261009.md`).
- **Pairing screen design** on the Mac is functional only. The design lane owns how it looks.
- **A Mac can pair at most 20 phones.** Pairing a 21st removes the one used least recently, with a sealed notice.
- **Checks only a real iPhone can do** are listed in `/mnt/project-files/dervo/phone/iphone-checks.md` (shimmer mask, blur, keyboard, haptics, VoiceOver, Dynamic Type, camera, push, background reconnect).
