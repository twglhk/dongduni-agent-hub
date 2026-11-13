# SETUP.md

The setup playbook ships inside the package, so an installed copy has it too:

- in this repo: [agent_hub/guide/SETUP.md](agent_hub/guide/SETUP.md)
- anywhere `dah` is installed: run `dah guide` (`dah guide engines` for engines, `dah guide extend` for extensions)

Asked to set up agent-hub? Install the package (`uv tool install dongduni-agent-hub` or `pipx install dongduni-agent-hub`), then read that playbook and follow its phases in order: survey, ask, install, launch, reach the board from the phone, prove the loop with a request to the hub and one to infra.

Upgrading an existing workspace to 0.8.0: `dah update --dry-run`, then `dah update`. The notes it prints are in [docs/RELEASING.md](docs/RELEASING.md) under 0.8.0.
