
# NicheDocBot Simulator Mode
The simulator is not a separate fake version of the application. It drives the real application through the same HTTP entry points used by Slack:

- `/slack/events`
- `/slack/interactions`

The only substitution is outbound messaging. In simulator mode, the app uses `SimulatorAdapter` instead of `SlackAdapter`. This means outbound Slack-style messages are sent to the simulator webhook instead of the real Slack API.

This project has two Kubernetes workloads:
## Manifests

```text
k8s/03-app/deployment-prod.yaml
  Real app in Slack mode.

k8s/03-app/deployment-prod-simulator.yaml
  Real app in simulator mode. Same app deployment name: deployment/nichedocbot.

k8s/03-app/deployment-simulator.yaml
  Simulator driver service: deployment/nichedocbot-simulator.
```

## 1. Real app

Deployment:


deployment/nichedocbot

Service name: nichedocbot

Normal mode:
Slack user -> ngrok -> nichedocbot app -> real Slack API


## 2. Simulator

Deployment:
deployment/nichedocbot-simulator

Manifest: k8s/03-app/deployment-simulator.yaml

Simulator mode flow:
nichedocbot-simulator -> nichedocbot /slack/events
nichedocbot -> SimulatorAdapter -> nichedocbot-simulator /webhook
nichedocbot-simulator -> nichedocbot /slack/interactions

The simulator avoids adding a polling API such as:
```
GET /status/{thread_ts}
```
It listens to outbound UI messages from the app instead.

Start simulator service

The simulator deployment is safe by default with:

replicas: 0

Turn it on:
```
kubectl scale deployment nichedocbot-simulator --replicas=1
```
Turn it off:
```
kubectl scale deployment nichedocbot-simulator --replicas=0
```
Check logs:
```
kubectl logs deployment/nichedocbot-simulator --tail=300
```

### Put the app in simulator mode

The simulator deployment alone is not enough.

The real app deployment must also be configured to send outbound UI messages to the simulator instead of real Slack.

For repeatability, use the simulator-mode app manifest:

```bash
kubectl apply -f k8s/03-app/deployment-prod-simulator.yaml
kubectl rollout status deployment/nichedocbot
```
This manifest should configure the main nichedocbot container with:
```
- name: CHAT_ADAPTER
  value: "simulator"
- name: SIMULATOR_WEBHOOK_URL
  value: "http://nichedocbot-simulator:8080/webhook"
- name: APP_MODE
  value: "simulator"
```
Confirm:
```
kubectl get deployment nichedocbot -o yaml | grep -A3 -B2 "CHAT_ADAPTER\|SIMULATOR_WEBHOOK_URL\|APP_MODE"
```
Expected:
```
- name: CHAT_ADAPTER
  value: simulator
- name: SIMULATOR_WEBHOOK_URL
  value: http://nichedocbot-simulator:8080/webhook
- name: APP_MODE
  value: simulator
```
Then confirm the app selected the simulator adapter:
```
kubectl logs deployment/nichedocbot --tail=100 | grep -E 
"Using SimulatorAdapter for outbound chat messages"

```

Expected:

Using SimulatorAdapter for outbound chat messages


### Return the app to real Slack mode

Apply the normal production app manifest:

```bash
kubectl apply -f k8s/03-app/deployment-prod.yaml
kubectl rollout status deployment/nichedocbot
```
Confirm:
```
kubectl get deployment nichedocbot -o yaml | grep -A3 -B2 "CHAT_ADAPTER\|SIMULATOR_WEBHOOK_URL\|APP_MODE"
```
Expected:
```
- name: CHAT_ADAPTER
  value: slack
```
The normal Slack manifest should not include:
```
- name: SIMULATOR_WEBHOOK_URL
```
Then confirm the app selected the Slack adapter:
```
kubectl logs deployment/nichedocbot --tail=100 | grep -E 
"Using SimulatorAdapter|Using SlackAdapter"
```
Expected:
```
Using SlackAdapter for outbound chat messages
```

