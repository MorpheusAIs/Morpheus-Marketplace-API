# Security Cleanup Checklist

## ⚠️ CRITICAL ITEMS TO FIX BEFORE GITHUB MIGRATION

### 1. AWS Account Numbers (HIGH PRIORITY)
- [ ] `deploy-with-testing.sh` line 11: Replace `586794444026` with `${AWS_ACCOUNT_ID}`
- [ ] `backup_scripts/deploy-with-testing.sh` line 11: Replace `586794444026` with `${AWS_ACCOUNT_ID}`

### 2. Environment Variables Setup
- [x] Created `env.example` template
- [ ] Update deployment scripts to use environment variables
- [ ] Test with local .env file

### 3. Hardcoded Configuration Items
- [ ] Replace hardcoded Cognito User Pool ID: `us-east-2_tqCTHoSST`
- [ ] Replace hardcoded contract addresses in deployment scripts
- [ ] Make AWS regions configurable

### 4. Documentation Updates
- [ ] Update README.md to reference env.example
- [ ] Add deployment security notes
- [ ] Document required environment variables

## Files That Need Updates:
1. `deploy-with-testing.sh`
2. `backup_scripts/deploy-with-testing.sh` 
3. `src/core/config.py` (verify env var usage)
4. `awsonboard` and `awsonboard_mor` scripts
5. `README.md`

## Post-Migration Tasks:
- [ ] Set up GitHub Secrets for CI/CD
- [ ] Create GitHub Actions workflows
- [ ] Test deployment with environment variables
- [ ] Verify no secrets in git history

## Environment Variables Required:
- AWS_ACCOUNT_ID
- AWS_REGION
- AWS_ACCESS_KEY_ID (optional if using IAM roles)
- AWS_SECRET_ACCESS_KEY (optional if using IAM roles)
- COGNITO_USER_POOL_ID
- COGNITO_USER_POOL_CLIENT_ID
- COGNITO_DOMAIN
- CONTRACT_ADDRESS
- DATABASE_URL
