# Part 2 — Architecture

## Overview

This architecture covers the end-to-end data flow for HDB's Data Platform on AWS, addressing:

1. **Data Ingestion**: Batch ingestion from the public data.gov.sg API into a private VPC-based data lake, with support for large file transfers (>100 MB).
2. **Data Exploitation**: Secure, private connectivity from Tableau on AWS to Amazon Athena for interactive analytics, including the analyst access path to Tableau.

The design prioritizes **security** (network segmentation, private connectivity, encryption), **scalability** (serverless and auto-scaling compute), and **performance** (direct streaming, partitioned storage, serverless querying).

> **Conceptual note:** EventBridge, Step Functions, Athena, Glue, S3 and KMS are **regional AWS services** — they are *not* deployed inside the VPC. In the diagram they are drawn in a separate **AWS Regional Services** boundary on the right. VPC-based compute reaches them **privately** through Gateway and Interface (PrivateLink) VPC Endpoints. Only resources with an elastic network interface (ECS Fargate tasks, the VPC-attached Lambda, Tableau EC2, the ALB, and the Interface Endpoint ENIs) actually consume private-subnet IP space.

---

## Contents

- `hdb-data-platform-architecture.png` — Architecture diagram (PNG, for submission)
- `hdb-data-platform-architecture.html` — Source of the diagram (self-contained dark-themed SVG; open in any browser)
- `hdb-data-platform-architecture.drawio` — Editable draw.io / diagrams.net source
- `hdb-data-platform-architecture.html.bak`, `.png.bak` — Backup copies of the previous revision

> **Note:** The PNG is rendered from the HTML via a headless browser. The `.drawio` file can be opened and edited at [app.diagrams.net](https://app.diagrams.net) and re-exported to PNG.

---

## Diagram

Open `hdb-data-platform-architecture.html` in a web browser (or the `.png`) for the full view.

---

## Component Breakdown & Data Flow

### 1. External Actors

| Component | Role |
|-----------|------|
| **data.gov.sg** | Public-facing government API providing datasets for batch download. |
| **Data Science Team** | Tableau users who reach the platform over HTTPS via the public ALB. |

### 2. Edge / Public Subnet

| Component | Role |
|-----------|------|
| **NAT Gateway (per-AZ)** | Provides **outbound-only** internet access for private-subnet compute (Fargate) to reach data.gov.sg, while blocking unsolicited inbound traffic. One NAT Gateway is deployed **per Availability Zone** for high availability. |
| **Application Load Balancer + AWS WAF** | Public-facing entry point for analysts to reach Tableau Server. The ALB terminates TLS and forwards to the Tableau target group; **AWS WAF** (WebACL) protects against common web exploits. This is the controlled inbound path — Tableau itself stays in a private subnet. |

### 3. Data Ingestion Layer (Private Subnet)

| Component | Role |
|-----------|------|
| **Amazon EventBridge** *(regional)* | Time-based scheduler triggering the ingestion pipeline on a defined cadence (e.g., daily, weekly). |
| **AWS Step Functions** *(regional)* | Orchestrates the ingestion workflow: invokes Fargate via `RunTask (.sync)`, handles retries, errors, and parallel steps. |
| **Amazon ECS Fargate** | Serverless compute (in-VPC) for downloading large files (>100 MB). Fargate tasks stream the file directly into S3 via multipart upload, avoiding local storage constraints. CPU/memory scale per task size. |
| **AWS Lambda** (VPC-attached) | Lightweight post-processing: metadata extraction, file validation, checksum verification, and triggering the Glue Crawler. |
| **Security Group: sg-ingestion** | Restricts outbound traffic to HTTPS (:443) and AWS service endpoints only. |

**Ingestion Data Flow:**

```
EventBridge --schedule--> Step Functions --RunTask(.sync)--> ECS Fargate
                                                                  |
                              (HTTPS via NAT GW, outbound only)   |
                                          data.gov.sg <-----------+
                                                                  |
                                    (multipart upload via S3 Gateway Endpoint)
                                                                  v
                                                        Amazon S3 (raw/)
                                                                  |
                        Lambda --start crawler--> Glue Crawler --crawl--> Glue Data Catalog
```

### 4. Storage & Catalog Layer *(regional services)*

| Component | Role |
|-----------|------|
| **Amazon S3 — Data Lake** | Target data store. Medallion-style layout with `raw/` and `curated/` prefixes, partitioned by date for query efficiency. SSE-KMS encrypted. |
| **Amazon S3 — Query Results** | Dedicated bucket for Athena query output (a required Athena configuration). |
| **AWS Glue — Data Catalog + Crawler** | The Crawler scans S3 partitions on a schedule and populates table schemas in the Data Catalog, making data queryable by Athena and Tableau. |
| **AWS KMS** | Manages the CMK for SSE-KMS encryption of all objects in the S3 buckets. |
| **CloudTrail · VPC Flow Logs** | Audit and network-flow monitoring across the platform. |

### 5. Private Connectivity & VPC Endpoints

These endpoints live in the private subnets (as ENIs) and are the **doorways** from in-VPC compute to the regional services — keeping traffic on the AWS backbone and off the public internet / NAT path.

| Endpoint | Type | Role |
|----------|------|------|
| **S3** | Gateway | Private, high-bandwidth S3 traffic (ingestion writes + Athena result access). |
| **Athena** | Interface (PrivateLink) | Tableau's Athena API calls (`StartQueryExecution`, etc.) resolve to private IPs in the VPC. |
| **Glue** | Interface | Metadata lookups against the Glue Data Catalog. |
| **ECR (api + dkr)** | Interface | Fargate pulls its container image privately — otherwise image pulls traverse the NAT Gateway. |
| **CloudWatch Logs** | Interface | Fargate / Lambda ship logs privately. |
| **KMS / STS** | Interface | Private SSE-KMS key operations and role credential vending. |

> **Athena ↔ S3 clarification:** the Athena Interface Endpoint privatizes the **control-plane API calls** from Tableau. The actual **data scan of S3** is performed by the regional Athena service itself (reading the Data Lake and writing to the Query Results bucket) — this is why Athena, S3 and Glue are drawn as regional services rather than inside the VPC.

### 6. Data Exploitation / Analytics Layer (Private Subnet)

| Component | Role |
|-----------|------|
| **Amazon Athena** *(regional)* | Serverless SQL query engine. Uses the Glue Data Catalog for schema resolution and scans the S3 Data Lake; writes results to the Query Results bucket. Scales automatically. |
| **Tableau Server** | Self-hosted visualization platform on EC2 (within an Auto Scaling Group), in a private subnet. Connects to Athena via the **Athena JDBC Driver**, routed through the Athena Interface Endpoint. Reached by analysts only via the public ALB + WAF. |
| **Security Group: sg-analytics** | Restricts ingress to HTTPS (:443) from the ALB security group, and egress to the Athena endpoint. |

**Exploitation Data Flow:**

```
Data Science Team --HTTPS--> ALB + WAF --:443--> Tableau Server
                                                     |
                                       Athena JDBC Driver
                                                     v
                              Athena Interface Endpoint --PrivateLink--> Amazon Athena
                                                                              |
                                              schema (Glue) + scan (S3 Data Lake) + results (S3)
```

---

## Design Rationale

### Why ECS Fargate instead of Lambda for large file ingestion?

AWS Lambda has a **15-minute timeout**, a **10 GB memory limit**, and **512 MB–10 GB ephemeral storage**. While Lambda can handle chunked downloads, files >100 MB are more reliably ingested using **ECS Fargate**, which offers:

- No hard timeout (configurable task duration)
- Up to 120 GB memory and 16 vCPU per task
- Direct streaming to S3 via the AWS SDK (multipart upload)
- Docker-based flexibility for custom download logic

### Why draw the regional services outside the VPC?

EventBridge, Step Functions, Athena, Glue, S3 and KMS are **regional, serverless AWS services** — they have no presence inside a customer VPC. Drawing them in a separate boundary and connecting them through VPC Endpoints reflects how traffic actually flows and makes the "private connectivity" story precise: the VPC never contains these services; it contains the **endpoints** that reach them privately.

### Why VPC Endpoints for Athena (and the rest)?

The requirements explicitly call for **private connectivity to Athena**. By default, the Athena JDBC driver calls the **public Athena endpoint**. To keep this traffic private:

- An **Interface VPC Endpoint for Athena** is provisioned in the private subnet.
- DNS resolution within the VPC routes Athena API calls to the endpoint's private IPs.
- Traffic stays entirely on the AWS backbone.

The **Gateway Endpoint for S3** and Interface Endpoints for **Glue, ECR, CloudWatch Logs and KMS/STS** apply the same principle to the rest of the platform, so that ingestion writes, image pulls, logging, metadata and key operations also avoid the NAT / public path.

### Analyst access to Tableau

Tableau Server sits in a private subnet and is **not** directly reachable from the internet. Analysts reach it through a **public ALB fronted by AWS WAF**, which terminates TLS and forwards to the Tableau Auto Scaling Group. `sg-analytics` only accepts :443 ingress from the ALB's security group.

### Network Segmentation Summary

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     AWS VPC — HDB Data Platform (Multi-AZ)                 │
│  ┌───────────────────────────────┐                                        │
│  │  Public Subnet (per-AZ)       │  NAT GW (egress) · ALB + WAF (ingress) │
│  └───────────────────────────────┘                                        │
│  ┌──────────┐   ┌──────────────┐   ┌──────────┐                           │
│  │Ingestion │   │ VPC Endpoints│   │ Analytics│      via Endpoints ──────► │
│  │ Fargate  │   │ S3 Gateway   │   │ Tableau  │                            │
│  │ Lambda   │   │ Athena/Glue  │   │  (EC2)   │                            │
│  │          │   │ ECR/Logs/KMS │   │          │                            │
│  └──────────┘   └──────────────┘   └──────────┘                           │
└──────────────────────────────────────────────────────────────────────────┘
                                   │ (PrivateLink / Gateway)
                                   ▼
   AWS Regional Services: EventBridge · Step Functions · Athena · Glue ·
                          S3 (Data Lake + Results) · KMS · CloudTrail
```

---

## Security Considerations

### Network Segmentation

- **Public & Private subnets**: Only the NAT Gateways and the ALB reside in the public subnet. All compute, endpoints and analytics resources are in private subnets and are not directly reachable from the internet.
- **NAT Gateway (per-AZ)**: Outbound-only internet access for private-subnet compute; no unsolicited inbound. One per AZ for HA.
- **ALB + WAF**: The single controlled inbound path (analyst → Tableau), protected by a WAF WebACL.

### Private Connectivity

- **VPC Endpoints (S3, Athena, Glue, ECR, Logs, KMS/STS)**: Gateway and Interface Endpoints ensure AWS service traffic never traverses the public internet or the NAT path.

### Access Control & Encryption

- **Security Groups**: Per-tier SGs (`sg-ingestion`, `sg-analytics`) restrict ingress/egress to the minimum required ports (:443) and sources.
- **SSE-KMS encryption**: All S3 objects encrypted at rest with a KMS CMK; TLS 1.2+ in transit.
- **Least-privilege IAM**: Minimal-permission roles on Fargate task roles, Lambda execution roles, and Tableau EC2 instance profiles.

---

## Scalability & Performance

### Data Ingestion

- **ECS Fargate for large files**: up to 120 GB memory / 16 vCPU per task, no hard timeout.
- **Streaming download to S3**: multipart upload directly to S3, avoiding local disk I/O and memory pressure.
- **Step Functions orchestration**: retries, error handling, parallel task execution, wait states; extensible to multiple concurrent files.

### Data Exploitation

- **Athena serverless scaling**: no clusters to provision; scales with query complexity and data volume.
- **S3 partitioning**: date-based partitioning (`raw/year=YYYY/month=MM/day=DD/`) enables partition pruning, reducing scan cost and latency.
- **Tableau Auto Scaling**: Tableau Server runs in an Auto Scaling Group across AZs to absorb concurrent user sessions.

---

## Key Assumptions

| # | Assumption |
|---|------------|
| 1 | data.gov.sg provides a stable HTTPS endpoint supporting range requests or chunked downloads for large files. |
| 2 | The HDB VPC is deployed across **at least 2 Availability Zones** (NAT Gateways, subnets, and the Tableau ASG span AZs). |
| 3 | IAM policies enforce **least-privilege access** (not detailed here per the brief). Fargate tasks use task roles; Tableau EC2 uses instance profiles. |
| 4 | **AWS PrivateLink / Interface VPC Endpoints** for Athena (and Glue, ECR, Logs, KMS, STS) are available in the target Region (e.g., `ap-southeast-1`). |
| 5 | **Tableau Server** is self-managed on EC2 (not Tableau Cloud), allowing full control over network placement, security groups and endpoint routing. |
| 6 | The **AWS Glue Crawler** runs on a schedule (e.g., after each ingestion batch) to keep partition schemas in sync with S3. |
| 7 | S3 bucket policies enforce **encryption in transit** (TLS 1.2+) and **encryption at rest** (SSE-KMS). |
| 8 | **CloudTrail** and **VPC Flow Logs** are enabled for audit and network monitoring. |
| 9 | Analysts reach Tableau over HTTPS through the public ALB/WAF, backed by corporate identity (e.g., SAML/SSO into Tableau); the exact IdP is out of scope. |

---

## Security, Scalability, and Performance Checklist

| Concern | How the Architecture Addresses It |
|---------|-----------------------------------|
| **Security — Network Segmentation** | Public subnet contains only NAT Gateways and the ALB/WAF. All compute, endpoints and analytics reside in private subnets with per-tier Security Groups. |
| **Security — Private Connectivity** | Gateway + Interface VPC Endpoints (S3, Athena, Glue, ECR, Logs, KMS/STS) keep AWS service traffic off the public internet and NAT path. |
| **Security — Controlled Ingress** | Analyst access to Tableau only via public ALB + AWS WAF; Tableau stays private. |
| **Security — Encryption** | SSE-KMS at rest; TLS 1.2+ in flight. |
| **Scalability — Compute** | Fargate scales per job; Athena is serverless; Tableau runs in an Auto Scaling Group. |
| **Scalability — Storage** | S3 is virtually unlimited; partitioning enables efficient pruning. |
| **Performance — Large Files** | Fargate streams downloads directly to S3 via multipart upload. |
| **Performance — Query Speed** | Athena leverages S3 partitioning and the Glue Data Catalog for fast, cost-effective SQL. |
| **Performance / Cost — Connectivity** | VPC Endpoints provide high-bandwidth private connectivity and keep image-pull/log/S3 traffic off per-GB NAT processing. |
