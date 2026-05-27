# Kong + Konga Setup — TPBank Webhook

## Thông tin endpoints

| Server | Internal Endpoint |
|---|---|
| **S1 (Primary)** | `http://192.168.255.71:8443/webhook/tpbank/notification` |
| **S2 (Standby)** | `http://192.168.255.10:8443/webhook/tpbank/notification` |
| **Health check** | `GET /health` (cả 2 server) |

---

## 1. Tạo Upstream

**Konga → Upstreams → Add Upstream**

| Field | Value |
|---|---|
| Name | `tpbank-webhook-upstream` |
| Hash on | `none` (round-robin với weight) |

### Thêm Targets

Vào upstream vừa tạo → **Targets → Add Target**:

| Target | Weight | Vai trò |
|---|---|---|
| `192.168.255.71:8443` | `100` | Primary — ưu tiên xử lý toàn bộ traffic |
| `192.168.255.10:8443` | `1` | Standby — chỉ nhận traffic khi S1 unhealthy |

---

## 2. Bật Health Check (Active + Passive)

Vào **Upstream → Edit → Health Checks**:

### Active Health Check — Healthy
| Field | Value |
|---|---|
| Interval | `10` giây |
| HTTP Path | `/health` |
| Successes | `2` lần liên tiếp → mark healthy trở lại |

### Active Health Check — Unhealthy
| Field | Value |
|---|---|
| Interval | `5` giây |
| HTTP Path | `/health` |
| HTTP Failures | `2` lần liên tiếp → mark unhealthy, failover sang S2 |
| TCP Failures | `2` |

### Passive (Circuit Breaker)
| Field | Value |
|---|---|
| HTTP Failures | `3` |
| TCP Failures | `2` |

> **Hành vi failover**: S1 down → Kong tự động chuyển 100% traffic sang S2. S1 recover (2 lần health check thành công) → tự động trả traffic về S1.

---

## 3. Tạo Service

**Konga → Services → Add Service**

| Field | Value |
|---|---|
| Name | `tpbank-webhook-service` |
| Protocol | `http` |
| Host | `tpbank-webhook-upstream` |
| Port | `8443` |
| Path | `/webhook/tpbank/notification` |

---

## 4. Tạo Route

Vào Service vừa tạo → **Routes → Add Route**

| Field | Value |
|---|---|
| Name | `tpbank-webhook-route` |
| Paths | `/tpbank/notification` (path TPBank gọi vào Kong) |
| Methods | `POST` |
| Strip Path | `true` nếu Kong rewrite path, `false` nếu giữ nguyên |
| Protocols | `https` (Kong nhận HTTPS từ TPBank, forward HTTP sang upstream) |

> **Lưu ý Strip Path**: Kong expose `/tpbank/notification`, upstream cần `/webhook/tpbank/notification`.
> Đặt **Strip Path = false** và để full path trên Service là `/webhook/tpbank/notification`.

---

## 5. Plugins gợi ý

Thêm qua **Service → Plugins → Add Plugin**:

| Plugin | Mục đích | Config gợi ý |
|---|---|---|
| **Rate Limiting** | Giới hạn request flood từ TPBank | `100 req/min` |
| **Request Size Limiting** | Giới hạn payload | `10 MB` |
| **Request Termination** | Tắt nhanh khi bảo trì | Bật khi cần |
| **HTTP Log** | Forward log vào Loki/ELK | URL Loki endpoint |

---

## 6. Kiểm tra sau cấu hình

```bash
# Kiểm tra health check trực tiếp
curl http://192.168.255.71:8443/health
curl http://192.168.255.10:8443/health

# Test qua Kong (thay <kong-host> bằng IP/domain Kong gateway)
curl -X POST https://<kong-host>/tpbank/notification \
  -H "Content-Type: application/json" \
  -d '{"batchId":"test","transactions":[]}'
```

### Kiểm tra upstream status trên Konga
**Upstreams → `tpbank-webhook-upstream` → Health** — xem trạng thái từng target (HEALTHY / UNHEALTHY).

---

## 7. Simulate failover test

```bash
# Tắt S1
ssh -i ~/.ssh/id_rsa_gdata.pem \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "cd /opt/tpb-webhook && docker compose stop webhook-api"

# Gửi request vào Kong → phải tự động failover sang S2
curl -X POST https://<kong-host>/tpbank/notification \
  -H "Content-Type: application/json" \
  -d '{"batchId":"failover-test","transactions":[]}'

# Bật lại S1
ssh -i ~/.ssh/id_rsa_gdata.pem \
  -o ProxyCommand="ssh -i ~/.ssh/id_rsa_gdata.pem -W %h:%p root@103.226.250.227" \
  root@192.168.255.71 \
  "cd /opt/tpb-webhook && docker compose start webhook-api"
```
