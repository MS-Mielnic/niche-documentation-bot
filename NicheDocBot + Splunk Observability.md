# NicheDocBot + Splunk Observability Cloud Runbook

This runbook documents how to verify, run, troubleshoot, and restore the NicheDocBot Kubernetes deployment with Splunk Observability Cloud.

---

## 1. Current Architecture

### NicheDocBot App

```text
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
```

### NicheDocBot Simulator

```text
NicheDocBot simulator
  service.name = nichedocbot-simulator
  OTLP endpoint from same ConfigMap
        ↓
Splunk OpenTelemetry Collector
        ↓
Splunk Observability Cloud
```

The app and simulator no longer point to the old local debug collector. They now read the OTEL endpoint from:

```text
ConfigMap: otel-routing-config
Namespace: default
Key: OTEL_EXPORTER_OTLP_ENDPOINT
```

Current value:

```text
http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
```

---

## 2. After Reboot: Verify the Local Cluster

Run:

```bash
docker ps
kubectl config current-context
kubectl get nodes
```

Expected context:

```text
kind-nichedocbot-cluster
```

Expected nodes:

```text
nichedocbot-cluster-control-plane   Ready
nichedocbot-cluster-worker          Ready
nichedocbot-cluster-worker2         Ready
```

---

## 3. Verify App, Simulator, and Collector Pods

Run:

```bash
kubectl get pods -A
kubectl get svc -A
```

Expected important resources:

```text
default/nichedocbot
default/nichedocbot-simulator
splunk-otel/splunk-otel-splunk-otel-collector-agent-...
splunk-otel/splunk-otel-splunk-otel-collector-k8s-cluster-receiver-...
```

Check only the Splunk namespace:

```bash
kubectl get all -n splunk-otel
```

Expected:

```text
daemonset.apps/splunk-otel-splunk-otel-collector-agent   3/3 ready
deployment.apps/splunk-otel-splunk-otel-collector-k8s-cluster-receiver   1/1 ready
```

---

## 4. Verify the Splunk Helm Release

Run:

```bash
helm status splunk-otel -n splunk-otel
```

Expected:

```text
STATUS: deployed
DESCRIPTION: Install complete
```

The notes should confirm the collector is configured for this realm:

```text
us1
```

---

## 5. Verify the Splunk Token Secret Exists

Run:

```bash
kubectl get secret splunk-otel-secret -n splunk-otel
```

Expected:

```text
NAME                 TYPE     DATA
splunk-otel-secret   Opaque   1
```

Do not print or decode the secret unless you intentionally need to rotate or debug it.

---

## 6. Verify the OTEL Routing ConfigMap

Run:

```bash
kubectl get configmap otel-routing-config -n default -o yaml
```

Expected data:

```yaml
data:
  OTEL_EXPORTER_OTLP_ENDPOINT: http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
```

---

## 7. Verify App and Simulator Use the Splunk Collector

Run:

```bash
kubectl exec deployment/nichedocbot -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
kubectl exec deployment/nichedocbot-simulator -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
```

Expected for both:

```text
http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
```

If you see this instead:

```text
http://otel-collector-service:4317
```

then the app is still pointing to the old debug collector.

---

## 8. Verify the Simulator Can Reach the App

Run:

```bash
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
```

Expected:

```text
DNS lookup works
TCP OK
POST status: 200
{"status":"unhandled_event_type"}
```

The `unhandled_event_type` response is fine. It means the app route is reachable and handled the empty test payload.

---

## 9. Generate Fresh Simulator Traffic

Restart only the simulator:

```bash
kubectl rollout restart deployment/nichedocbot-simulator
kubectl rollout status deployment/nichedocbot-simulator
```

Then check simulator logs:

```bash
kubectl logs deployment/nichedocbot-simulator --tail=160
```

You want to see simulator flows like:

```text
Starting flow for topic: kubernetes
Sending POST to http://nichedocbot:8000/slack/events
```

You do not want to see:

```text
Scenario failed: All connection attempts failed
```

Check app logs:

```bash
kubectl logs deployment/nichedocbot --tail=160
```

---

## 10. Verify Traces in Splunk Observability Cloud

In Splunk Observability Cloud, search for these services:

```text
nichedocbot
nichedocbot-simulator
```

Use the time window around the simulator run.

You should also be able to see the earlier test service:

```text
otel-trace-test
```

---

## 11. Optional Controlled Trace Test

Use this to prove the collector path independently of the app:

```bash
kubectl run otel-trace-test \
  --rm -i --restart=Never \
  --namespace default \
  --image=ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:latest \
  -- traces \
  --otlp-endpoint splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317 \
  --otlp-insecure \
  --traces 5 \
  --service otel-trace-test
```

Expected:

```text
Channel Connectivity change to READY
traces generate {"worker": 0, "traces": 5}
pod "otel-trace-test" deleted
```

Then search for this service in Splunk APM or traces:

```text
otel-trace-test
```

---

## 12. Known Non-Blocking Warnings

The Splunk collector may show Kind-related Kubernetes metrics warnings like:

```text
kubeletstats ... tls: failed to verify certificate
x509: cannot validate certificate for node IP because it doesn't contain any IP SANs
```

This affects local Kind kubelet and node metrics scraping.

It does not block:

```text
nichedocbot traces
nichedocbot-simulator traces
OTLP/gRPC trace ingestion
Splunk APM visibility
```

Do not chase this first unless the next goal is Kubernetes infrastructure metrics quality.

---

## 13. Old Debug Collector Still Present

The old local debug collector still exists:

```text
namespace: default
daemonset: otel-collector
service: otel-collector-service
configmap: otel-collector-conf
```

It was useful before Splunk was installed because it printed traces to logs.

Current app traffic no longer uses it because the app now reads the Splunk endpoint from:

```text
otel-routing-config
```

To verify:

```bash
kubectl get daemonset otel-collector -n default
kubectl get service otel-collector-service -n default
```

Recommendation: keep it for now until the Splunk setup is fully documented and committed.

---

## 14. Rollback Path to the Old Debug Collector

If Splunk ingestion breaks and you want to route app traces back to the old debug collector, update:

```bash
kubectl edit configmap otel-routing-config -n default
```

Change:

```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
```

Back to:

```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector-service:4317
```

Then restart app and simulator so they reload the environment variable:

```bash
kubectl rollout restart deployment/nichedocbot
kubectl rollout restart deployment/nichedocbot-simulator
kubectl rollout status deployment/nichedocbot
kubectl rollout status deployment/nichedocbot-simulator
```

Confirm:

```bash
kubectl exec deployment/nichedocbot -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
kubectl exec deployment/nichedocbot-simulator -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
```

Expected rollback value:

```text
http://otel-collector-service:4317
```

---

## 15. Important Files in the Repo

Splunk values file:

```text
k8s/04-observability/splunk-values.yaml
```

OTEL routing ConfigMap:

```text
k8s/04-observability/otel-routing-config.yaml
```

Active app deployment for simulator mode:

```text
k8s/03-app/deployment-prod-simulator.yaml
```

Simulator deployment:

```text
k8s/03-app/deployment-simulator.yaml
```

Non-simulator production app variant:

```text
k8s/03-app/deployment-prod.yaml
```

---

## 16. Current Success Definition

The setup is successful when all of these are true:

* Kind cluster is running.
* Splunk collector pods are running.
* Helm release `splunk-otel` is deployed.
* `otel-routing-config` points to the Splunk collector.
* `nichedocbot` and `nichedocbot-simulator` resolve the Splunk OTEL endpoint.
* Simulator can POST to `nichedocbot:8000/slack/events`.
* Splunk Observability Cloud shows:

  * `nichedocbot`
  * `nichedocbot-simulator`
  * optional `otel-trace-test`

Current state:

```text
successful
```

---

## 17. RAG and LLM Telemetry Enrichment

### Trace Attributes

```text
workflow.type
rag.repo
rag.local_db_found
rag.retrieval.chunk_count
rag.retrieval.top_score
rag.retrieval.duration_ms
llm.model
llm.is_multimodal
llm.duration_ms
llm.prompt_tokens
llm.completion_tokens
llm.total_tokens
```

### Custom Metrics

```text
rag.retrieval.duration_ms_*
rag.retrieval.chunk_count_*
rag.retrieval.top_score_*
llm.duration_ms_*
llm.prompt_tokens_*
llm.completion_tokens_*
llm.total_tokens_*
```

### Splunk Histogram Naming

OpenTelemetry histograms appear in Splunk as:

```text
_sum
_count
_min
_max
_bucket
```

For averages, use formulas such as:

```text
llm.duration_ms_sum / llm.duration_ms_count
llm.total_tokens_sum / llm.total_tokens_count
rag.retrieval.top_score_sum / rag.retrieval.top_score_count
```

### Correct Kind Image Load Command

Use:

```bash
kind load docker-image nichedocbot:latest --name nichedocbot-cluster
```

Plain `kind load docker-image nichedocbot:latest` fails when the cluster is not named `kind`.

---

# Troubleshooting: Splunk APM Stops Showing App Services

Use this section when Splunk APM stops showing `nichedocbot` or `nichedocbot-simulator`, or when it looks like no telemetry is reaching Splunk.

---

## 1. Separate the Possible Failure Points

The telemetry path is:

```text
app / simulator → Splunk OTel Collector in Kubernetes → Splunk Observability Cloud
```

Do not assume the app is broken immediately. Check each part of the path.

---

## 2. Verify the Splunk OTel Collector Is Running

```bash
kubectl get pods -n splunk-otel
kubectl get svc -n splunk-otel
helm status splunk-otel -n splunk-otel
```

Expected:

```text
splunk-otel-splunk-otel-collector-agent-*   1/1 Running
splunk-otel-splunk-otel-collector-k8s-cluster-receiver-*   1/1 Running
STATUS: deployed
```

---

## 3. Verify App and Simulator Point to the Splunk Collector

```bash
kubectl exec deployment/nichedocbot -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
kubectl exec deployment/nichedocbot-simulator -- printenv OTEL_EXPORTER_OTLP_ENDPOINT
```

Expected:

```text
http://splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317
```

---

## 4. Send a Synthetic Trace Directly to the Collector

```bash
TEST_SERVICE="otel-trace-test-$(date +%H%M%S)"

echo "Testing service: $TEST_SERVICE"

kubectl run "$TEST_SERVICE" \
  --rm -i --restart=Never \
  --namespace default \
  --image=ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:latest \
  -- traces \
  --otlp-endpoint splunk-otel-splunk-otel-collector-agent.splunk-otel.svc.cluster.local:4317 \
  --otlp-insecure \
  --traces 20 \
  --service "$TEST_SERVICE"
```

Then search for the printed service name in Splunk APM using a “last 15 minutes” or “last 30 minutes” window.

Interpretation:

```text
If the test service appears in Splunk:
  Kubernetes → Splunk Collector → Splunk Cloud is working.

If the test service does not appear:
  Debug collector export, Splunk token, realm, or network egress.
```

---

## 5. Verify Egress from Kubernetes to Splunk Cloud

```bash
kubectl run curl-splunk-ingest \
  --rm -i --restart=Never \
  --namespace default \
  --image=curlimages/curl:latest \
  -- sh -c 'date; curl -I -m 10 https://ingest.us1.observability.splunkcloud.com'
```

Expected:

```text
HTTP/2 404
```

A `404` is acceptable here because the root endpoint is not a real ingest API route. The important thing is that DNS, TLS, and outbound network access work.

---

## 6. Check Collector Export Errors

```bash
for pod in $(kubectl get pods -n splunk-otel -o name | grep 'collector-agent'); do
  echo "===== $pod ====="
  kubectl logs -n splunk-otel "$pod" --since=45m | \
    grep -Ei "otelcol.signal.*trace|traces|otlphttp|splunk|signalfx|exporting failed|retry|timeout|deadline|unauthorized|forbidden|dropped" | \
    tail -120
done
```

Known non-blocking warnings in local Kind:

```text
kubeletstats TLS/IP SAN errors
Prometheus kubernetes-proxy scrape warnings
intermittent signalfx metrics export timeout warnings
```

These may affect infrastructure metrics, but they do not necessarily mean APM traces are broken.

A metrics timeout looks like:

```text
otelcol.signal: "metrics"
otelcol.component.id: "signalfx"
Post "https://ingest.us1.observability.splunkcloud.com/v2/datapoint":
Client.Timeout exceeded while awaiting headers
```

If this appears only for `metrics`, and synthetic traces still appear in Splunk, then trace export is working.

---

## 7. Verify Simulator Image and Runtime Status

Because the simulator uses a local Kind image tag, confirm the pod is running the expected image:

```bash
kubectl get pods
kubectl get deployment nichedocbot-simulator -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl describe pod -l app=nichedocbot-simulator | grep -Ei "Image:|Image ID:|Pull|Failed|BackOff|Err"
```

Expected image:

```text
nichedocbot-simulator:otel-httpx-v1
```

If the image is missing after a rebuild or Kind reset:

```bash
docker images | grep nichedocbot-simulator
kind load docker-image nichedocbot-simulator:otel-httpx-v1 --name nichedocbot-cluster
kubectl rollout restart deployment/nichedocbot-simulator
kubectl rollout status deployment/nichedocbot-simulator
```

---

## 8. Verify Simulator Is Generating App Traffic

```bash
kubectl logs deployment/nichedocbot-simulator --tail=150
kubectl logs deployment/nichedocbot --tail=150
```

Look for app logs like:

```text
POST /slack/events HTTP/1.1" 200 OK
POST /slack/interactions HTTP/1.1" 200 OK
HTTP Request: POST http://nichedocbot-simulator:8080/webhook "HTTP/1.1 200 OK"
```

If the simulator is quiet, force new traffic:

```bash
kubectl rollout restart deployment/nichedocbot-simulator
kubectl rollout status deployment/nichedocbot-simulator
kubectl logs deployment/nichedocbot-simulator --tail=150 -f
```

---

## 9. Verify App Instrumentation Packages

```bash
kubectl exec -i deployment/nichedocbot -- python - <<'PY'
mods = [
    "opentelemetry.instrumentation.fastapi",
    "opentelemetry.instrumentation.httpx",
    "opentelemetry.instrumentation.sqlite3",
]
for m in mods:
    try:
        __import__(m)
        print(f"OK {m}")
    except Exception as e:
        print(f"MISSING {m}: {e}")
PY
```

Expected:

```text
OK opentelemetry.instrumentation.fastapi
OK opentelemetry.instrumentation.httpx
OK opentelemetry.instrumentation.sqlite3
```

---

## 10. Test Trace Export from Inside the App Pod

```bash
kubectl exec -i deployment/nichedocbot -- python - <<'PY'
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
import time
import os

endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
print("endpoint:", endpoint)

provider = TracerProvider(
    resource=Resource.create({"service.name": "nichedocbot-app-pod-export-test"})
)
processor = BatchSpanProcessor(
    OTLPSpanExporter(endpoint=endpoint, insecure=True)
)
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("manual-test")
with tracer.start_as_current_span("manual.app.pod.export.test") as span:
    span.set_attribute("test.source", "nichedocbot-pod")
    print("created span")

provider.force_flush()
time.sleep(2)
provider.shutdown()
print("done")
PY
```

Then search Splunk APM for:

```text
nichedocbot-app-pod-export-test
```

Interpretation:

```text
If this appears in Splunk:
  The app pod can export traces. The collector/Splunk path is working.

If normal app traffic still does not appear:
  Check whether the app is generating fresh traffic and whether the Splunk APM time window or filters are hiding the service.
```

---

## 11. Confirm Direct App Service Reachability

```bash
kubectl run app-direct-test \
  --rm -i --restart=Never \
  --namespace default \
  --image=curlimages/curl:latest \
  -- sh -c 'curl -s -o /dev/null -w "%{http_code}\n" -X POST http://nichedocbot:8000/slack/events -H "Content-Type: application/json" -d "{}"'
```

Expected:

```text
200
```

---

