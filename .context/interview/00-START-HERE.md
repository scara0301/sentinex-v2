# SENTINEX — interview preparation

## How to use this

Read in order. `01` is the story you tell, `02`–`04` are the depth behind it,
`05` is the interrogation, `06` is your strongest differentiator.

| File | What it gives you |
|---|---|
| `01-how-i-built-it.md` | The build narrative — order, and the decision at each step. |
| `02-architecture-and-why.md` | Every component: why it exists, what else you considered, why this won. |
| `03-code-walkthrough.md` | Module by module, function by function. |
| `04-containers-and-infra.md` | Every container and infra element, and every hardening flag. |
| `05-hard-questions.md` | The questions that are actually hard, with answers. |
| `06-bugs-i-found-and-fixed.md` | Debugging stories with evidence. Usually the best material you have. |

One warning before you start. Do not memorise these as scripts. An interviewer
finds the edge of memorised knowledge in about two follow-ups, and the drop-off
is obvious and damaging. Understand the *reasoning*, then say it in your own
words. Where you genuinely do not know something, say so — "I didn't implement
that, here's how I'd approach it" is a strong answer. Bluffing is not.

---

## The 90-second pitch

> SENTINEX is a runtime security scanner for AI agents. Static analysis cannot
> tell you whether an agent will actually exfiltrate data, because the
> behaviour depends on what the model decides at runtime. So instead of reading
> the code, I run the agent in a disposable sandbox with no network egress,
> force all its traffic through an intercepting proxy, feed it deliberately
> poisoned tool responses, and record every call it makes.
>
> Then I evaluate detection rules over that recorded event stream. If the agent
> follows an instruction injected into a tool response and contacts an attacker
> host, that produces a critical finding with the exact event sequence as
> evidence.
>
> The architecture is four services: a FastAPI control plane, an arq worker
> that owns the Docker orchestration, a mitmproxy addon that does the
> interception, and a Next.js dashboard that streams events live over
> WebSockets. State machine per scan, event-sourced, with a shared Redis
> sequence counter so three independent producers can write to one ordered
> event log.

**Then stop talking.** Let them pick the thread. Do not pre-empt with a tour of
every feature.

---

## The one-sentence version of each core idea

Keep these crisp. They are the load-bearing claims.

- **Why runtime, not static:** an agent's danger is a property of its
  behaviour under adversarial input, not of its source text.
- **Why a proxy:** it is the one place every tool call must pass through,
  regardless of which framework or SDK the agent uses.
- **Why an internal Docker network:** it makes the proxy unavoidable rather
  than merely configured. The agent cannot route around it.
- **Why event sourcing:** detections run after the fact over an ordered log,
  so the same rules work for live scans and replays, and every finding cites
  the exact events that produced it.
- **Why noisy-OR scoring:** it makes the score monotonic. Finding one more
  vulnerability must never lower reported risk.
- **Why honeypots:** planted fake credentials give exfiltration detection a
  ground truth. If a value that only exists inside the sandbox leaves it, that
  is not a heuristic, it is proof.

---

## Numbers worth knowing

| Thing | Value |
|---|---|
| Services | 4 (API, worker, proxy, mocks) + shared core package + Next.js dashboard |
| Scan states | 10 |
| Framework loaders | 6 (langchain, crewai, autogen, openai_assistants, mcp, raw_python) |
| Built-in scenarios | 3 |
| Unit tests | 167 |
| Migrations | 6 |

Do not recite these unprompted. They are for when you are asked "how big is
it".
