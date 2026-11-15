# QoS-Aware Voice over UDP

G.711 µ-law voice over RTP/UDP with a jitter buffer, loss concealment, DSCP EF marking
and a strict-priority queue on a router namespace. Quality is measured with RFC 3550
jitter, loss, late loss, one-way delay and a simplified G.107 E-model (R-factor and MOS).

## Required packages

### Python (installed from `requirements.txt`)

| Package | Used for |
|---|---|
| numpy | µ-law codec, audio frame handling |
| matplotlib | experiment plots |
| pyyaml | reading `experiments/scenarios.yaml` |
| pytest | unit tests |

Python 3.10 or newer is recommended. The standard library covers the rest
(`asyncio`, `socket`, `struct`, `wave`, `json`, `csv`).

### System tools (installed with apt)

| Tool | Used for |
|---|---|
| iproute2 (`ip`, `tc`) | namespaces, veth pairs, netem, prio/tbf queueing |
| iperf3 | bulk UDP load to congest the router |
| wireshark / tshark | inspect RTP and DSCP (Decode As → RTP) |
| traceroute | optional: confirm the router is in the path |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

sudo apt update
sudo apt install iproute2 iperf3 wireshark traceroute
```

## Build the network

Needs root.

```bash
chmod +x netlab/*.sh
sudo netlab/topo_up.sh
sudo ip netns exec ns-a ping -c 3 10.0.2.2    # expect ttl=63 (router is in the path)
sudo netlab/topo_down.sh                      # tear down when finished
```

Topology: `ns-a (10.0.1.2) <-> ns-router (10.0.1.1 / 10.0.2.1) <-> ns-b (10.0.2.2)`

## Running Python inside a namespace

`sudo` does not see your virtualenv, so call the venv interpreter directly:

```bash
sudo ip netns exec ns-b .venv/bin/python -m apps.call_callee ...
sudo ip netns exec ns-a .venv/bin/python -m apps.call_caller --peer 10.0.2.2 ...
```

## Tests

No root needed.

```bash
pytest
```