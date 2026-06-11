NicheDocBot + Splunk Observability Cloud Runbook
1. Current architecture
NicheDocBot app
  service.name = nichedocbot
  OTLP endpoint from ConfigMap
        ↓
Splunk OpenTelemetry Collector
  namespace = splunk-otel
  Helm release = splunk-otel
        ↓
Splunk Observability Cloud
  realm = us1

NicheDocBot simulator
  service.name = nichedocbot-simulator
  OTLP endpoint from same ConfigMap
        ↓
Splunk OpenTelemetry Collector
        ↓
Splunk Observability Cloud

The app and simulator no longer point to the old local debug collector. They now read the OTEL endpoint from:

ConfigMap: otel-routing-config
Namespace: default
Key: OTEL_EXPORTER_OTLP_ENDPOINT

Current value:

http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
2. After reboot: verify the local cluster

Run:

docker ps
kubectl config current-context
kubectl get nodes

Expected context:

kind-nichedocbot-cluster

Expected nodes:

nichedocbot-cluster-control-plane   Ready
nichedocbot-cluster-worker          Ready
nichedocbot-cluster-worker2         Ready
3. Verify app, simulator, and collector pods

Run:

kubectl get pods -A
kubectl get svc -A

Expected important resources:

default/nichedocbot
default/nichedocbot-simulator
splunk-otel/splunk-otel-splunk-otel-collector-agent-...
splunk-otel/splunk-otel-splunk-otel-collector-k8s-cluster-receiver-...

Check only the Splunk namespace:

kubectl get all -n splunk-otel

Expected:

daemonset.apps/splunk-otel-splunk-otel-collector-agent   3/3 ready
deployment.apps/splunk-otel-splunk-otel-collector-k8s-cluster-receiver   1/1 ready
4. Verify Splunk Helm release

Run:

helm status splunk-otel -n splunk-otel

Expected:

STATUS: deployed
DESCRIPTION: Install complete

The notes should say the collector is configured for realm:

us1
5. Verify Splunk token secret exists

Run:

kubectl get secret splunk-otel-secret -n splunk-otel

Expected:

NAME                 TYPE     DATA
splunk-otel-secret   Opaque   1

Do not print or decode the secret unless you intentionally need to rotate/debug it.

6. Verify OTEL routing ConfigMap

Run:

kubectl get configmap otel-routing-config -n default -o yaml

Expected data:

data:
  OTEL_EXPORTER_OTLP_ENDPOINT: http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
7. Verify app and simulator are using the Splunk collector

Run:

kubectl exec deployment/nichedocbot -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
kubectl exec deployment/nichedocbot-simulator -- printenv OTEL_EXPORTER_OTLP_ENDPOINT

Expected for both:

http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317

If you see this instead:

http://otel-collector-service:4317

then the app is still pointing to the old debug collector.

8. Verify simulator can reach the app

Run:

kubectl exec -i deployment/nichedocbot-simulator -- python - <<'PY'
import socket
import urllib.request

host = "nichedocbot"
port = 8000

print("DNS lookup:")
print(socket.gethostbyname_ex(host))

print("\nTCP connect:")
s = socket.create_connection((host, port), timeout=5)
print("TCP OK")
s.close()

print("\nPOST /slack/events:")
req = urllib.request.Request(
    "http://nichedocbot:8000/slack/events",
    data=b"{}",
    headers={"Content-Type": "application/json"},
    method="POST",
)
r = urllib.request.urlopen(req, timeout=5)
print("POST status:", r.status)
print(r.read(300).decode("utf-8", errors="replace"))
PY

Expected:

DNS lookup works
TCP OK
POST status: 200
{"status":"unhandled_event_type"}

The unhandled_event_type response is fine. It means the app route is reachable and handled the empty test payload.

9. Generate fresh simulator traffic

Restart only the simulator:

kubectl rollout restart deployment/nichedocbot-simulator
kubectl rollout status deployment/nichedocbot-simulator

Then check simulator logs:

kubectl logs deployment/nichedocbot-simulator --tail=160

You want to see simulator flows like:

Starting flow for topic: kubernetes
Sending POST to http://nichedocbot:8000/slack/events

You do not want to see:

Scenario failed: All connection attempts failed

Check app logs:

kubectl logs deployment/nichedocbot --tail=160
10. Verify traces in Splunk Observability Cloud

In Splunk Observability Cloud, search for these services:

nichedocbot
nichedocbot-simulator

Use the time window around the simulator run.

You should also be able to see the earlier test service:

otel-trace-test
11. Optional controlled trace test

Use this if you want to prove the collector path independently of the app:

kubectl run otel-trace-test \
  --rm -i --restart=Never \
  --namespace default \
  --image=ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:latest \
  -- traces \
  --otlp-endpoint splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317 \
  --otlp-insecure \
  --traces 5 \
  --service otel-trace-test

Expected:

Channel Connectivity change to READY
traces generate {"worker": 0, "traces": 5}
pod "otel-trace-test" deleted

Then search for:

otel-trace-test

in Splunk APM/traces.

12. Known non-blocking warnings

The Splunk collector may show Kind-related Kubernetes metrics warnings like:

kubeletstats ... tls: failed to verify certificate
x509: cannot validate certificate for node IP because it doesn't contain any IP SANs

This affects local Kind kubelet/node metrics scraping.

It does not block:

nichedocbot traces
nichedocbot-simulator traces
OTLP/gRPC trace ingestion
Splunk APM visibility

Do not chase this first unless the next goal is Kubernetes infrastructure metrics quality.

13. Old debug collector still present

The old local debug collector still exists:

namespace: default
daemonset: otel-collector
service: otel-collector-service
configmap: otel-collector-conf

It was useful before Splunk was installed because it printed traces to logs.

Current app traffic no longer uses it because the app now reads the Splunk endpoint from otel-routing-config.

To verify:

kubectl get daemonset otel-collector -n default
kubectl get service otel-collector-service -n default

Recommended: keep it for now until the Splunk setup is fully documented and committed.

14. Rollback path to old debug collector

If Splunk ingestion breaks and you want to route app traces back to the old debug collector, update:

kubectl edit configmap otel-routing-config -n default

Change:

OTEL_EXPORTER_OTLP_ENDPOINT: http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317

back to:

OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector-service:4317

Then restart app and simulator so they reload the environment variable:

kubectl rollout restart deployment/nichedocbot
kubectl rollout restart deployment/nichedocbot-simulator
kubectl rollout status deployment/nichedocbot
kubectl rollout status deployment/nichedocbot-simulator

Then confirm:

kubectl exec deployment/nichedocbot -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
kubectl exec deployment/nichedocbot-simulator -- printenv OTEL_EXPORTER_OTLP_ENDPOINT

Expected rollback value:

http://otel-collector-service:4317
15. Important files in the repo

Splunk values file:

k8s/04-observability/splunk-values.yaml

OTEL routing ConfigMap:

k8s/04-observability/otel-routing-config.yaml

Active app deployment for simulator mode:

k8s/03-app/deployment-prod-simulator.yaml

Simulator deployment:

k8s/03-app/deployment-simulator.yaml

Non-simulator production app variant:

k8s/03-app/deployment-prod.yaml
16. Current success definition

The setup is successful when all of these are true:

Kind cluster is running.
Splunk collector pods are Running.
Helm release splunk-otel is deployed.
otel-routing-config points to Splunk collector.
nichedocbot and nichedocbot-simulator resolve the Splunk OTEL endpoint.
Simulator can POST to nichedocbot:8000/slack/events.
Splunk Observability Cloud shows:
  - nichedocbot
  - nichedocbot-simulator
  - optional otel-trace-test

Current state: successful.