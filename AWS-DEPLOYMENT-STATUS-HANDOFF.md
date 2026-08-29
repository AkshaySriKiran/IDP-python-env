# AWS Deployment Status — LIVE & VERIFIED ✅

**Project:** DocuLoom  
**AWS Account ID:** `912564796433`  
**Region:** `eu-west-1` (Ireland)  
**CloudFront Live URL:** `https://d11bl7hg497hj.cloudfront.net`  
**CloudFront Distribution ID:** `E10ZYX1C43T9R6`  
**ECS Cluster / Service:** `omniparse-idp-cluster` / `omniparse-idp-api`  
**ECR Repository:** `912564796433.dkr.ecr.eu-west-1.amazonaws.com/omniparse-idp-api:latest`  
**S3 UI Bucket:** `omniparse-idp-ui-912564796433`  
**Deployment Status:** **LIVE & HEALTHY (ACTIVE 1/1)**  

---

## 1. Live Deployment Verification

- **Production Health Check**:
  - `GET https://d11bl7hg497hj.cloudfront.net/api/health` → `HTTP 200 OK`
  - Response:
    ```json
    {
      "status": "ok",
      "service": "omniparse-maintenance-api",
      "version": "1.0.0",
      "busy": false,
      "queue_depth": 0
    }
    ```
- **ECS Fargate Tasks**: Steady state (`ACTIVE 1/1`), `JWT_SECRET` configured.
- **Frontend Assets**: Updated with **DocuLoom** branding, 24-hour cryptographic share URLs, and clean extraction tables.

---

## 2. Features Active in Production

1. **Brand Identity**: Full **DocuLoom** branding across workspace, landing pages, history, and admin console.
2. **24-Hour Cryptographic Sharing**:
   - `POST /api/fabric/extracts/{run_id}/share` generates secure, temporary read-only share URLs.
   - `GET /api/share/{token}` serves the sanitized extraction table (Maintenance, Spare Parts, Troubleshooting) with zero login required for anonymous viewers.
   - Separate header status badges for Shared View and 24-hour expiration countdown.
   - Internal columns (`Confidence`, `Why low score`, `Actions`) hidden on shared view for a clean recipient experience.
3. **Microsoft Fabric Sync**: Broadened document extraction status sync (`Approved`, `Needs Revision`, `Pending Review`) with live updates across My Extracts and Admin Extraction Logs.
