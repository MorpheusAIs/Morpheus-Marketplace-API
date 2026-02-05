-- Model Switch Rate Limiting - Test Queries
-- Run these to verify the implementation works correctly

-- ============================================================================
-- PRE-DEPLOYMENT: Verify table doesn't exist yet
-- ============================================================================

SELECT table_name 
FROM information_schema.tables 
WHERE table_name = 'api_key_model_switches';
-- Expected: No rows (table doesn't exist yet)

-- ============================================================================
-- POST-MIGRATION: Verify table created successfully
-- ============================================================================

\d api_key_model_switches;
-- Expected: Table structure with columns:
--   id, api_key_id, user_id, from_model, to_model, switched_at, created_at

SELECT * FROM api_key_model_switches LIMIT 1;
-- Expected: No rows (table empty until rate limiting enabled)

-- ============================================================================
-- FIND USER IDS FOR EXEMPTIONS
-- ============================================================================

-- Find Milan's user ID
SELECT id, email, created_at 
FROM users 
WHERE email = 'ENTEREMAILHERE';
-- Result: id = 3

-- Find other potential problem users
SELECT 
  u.id,
  u.email,
  COUNT(DISTINCT s.id) as sessions_24h,
  COUNT(DISTINCT s.model) as models_used
FROM users u
JOIN sessions s ON s.user_id = u.id
WHERE s.created_at >= NOW() - INTERVAL '24 hours'
GROUP BY u.id, u.email
ORDER BY sessions_24h DESC
LIMIT 10;

-- ============================================================================
-- AFTER ENABLING: Monitor switch activity
-- ============================================================================

-- Real-time switch monitoring (updates every few seconds)
SELECT 
  u.email,
  ak.key_prefix as api_key,
  COUNT(*) as switches_last_hour,
  MAX(switched_at) as last_switch
FROM api_key_model_switches ams
JOIN users u ON ams.user_id = u.id
JOIN api_keys ak ON ams.api_key_id = ak.id
WHERE switched_at >= NOW() - INTERVAL '1 hour'
GROUP BY u.email, ak.key_prefix
ORDER BY switches_last_hour DESC;

-- Check who's hitting limits
SELECT 
  u.id,
  u.email,
  COUNT(*) as switches_last_hour,
  CASE 
    WHEN COUNT(*) >= 10 THEN '🚫 RATE LIMITED'
    WHEN COUNT(*) >= 8 THEN '⚠️  APPROACHING LIMIT'
    ELSE '✅ NORMAL'
  END as status
FROM api_key_model_switches ams
JOIN users u ON ams.user_id = u.id
WHERE switched_at >= NOW() - INTERVAL '1 hour'
GROUP BY u.id, u.email
ORDER BY switches_last_hour DESC;

-- Daily switch summary
SELECT 
  u.email,
  ak.key_prefix,
  DATE(switched_at) as date,
  COUNT(*) as switches,
  COUNT(DISTINCT to_model) as unique_models
FROM api_key_model_switches ams
JOIN users u ON ams.user_id = u.id
JOIN api_keys ak ON ams.api_key_id = ak.id
WHERE switched_at >= NOW() - INTERVAL '7 days'
GROUP BY u.email, ak.key_prefix, DATE(switched_at)
ORDER BY date DESC, switches DESC;

-- Model switching patterns
SELECT 
  SUBSTRING(from_model, 1, 20) || '...' as from_model,
  SUBSTRING(to_model, 1, 20) || '...' as to_model,
  COUNT(*) as switch_count,
  AVG(EXTRACT(EPOCH FROM (switched_at - LAG(switched_at) OVER (ORDER BY switched_at)))) as avg_seconds_between
FROM api_key_model_switches
WHERE user_id = 3  -- Milan's user ID
  AND switched_at >= NOW() - INTERVAL '24 hours'
GROUP BY from_model, to_model
ORDER BY switch_count DESC
LIMIT 10;

-- ============================================================================
-- VERIFY EXEMPTIONS ARE WORKING
-- ============================================================================

-- After adding exemption, check user is NOT being rate limited
SELECT 
  u.id,
  u.email,
  COUNT(*) as switches_last_hour,
  CASE 
    WHEN u.id IN (3) THEN '✅ EXEMPT BY USER_ID'
    WHEN u.email IN ('emailuser') THEN '✅ EXEMPT BY EMAIL'
    WHEN COUNT(*) >= 10 THEN '🚫 SHOULD BE LIMITED'
    ELSE '✅ UNDER LIMIT'
  END as status
FROM api_key_model_switches ams
JOIN users u ON ams.user_id = u.id
WHERE switched_at >= NOW() - INTERVAL '1 hour'
GROUP BY u.id, u.email
ORDER BY switches_last_hour DESC;

-- ============================================================================
-- CLEANUP OLD DATA (Run periodically to prevent table bloat)
-- ============================================================================

-- Delete switches older than 30 days
DELETE FROM api_key_model_switches 
WHERE switched_at < NOW() - INTERVAL '30 days';

-- Check table size
SELECT 
  COUNT(*) as total_records,
  COUNT(CASE WHEN switched_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as last_24h,
  COUNT(CASE WHEN switched_at >= NOW() - INTERVAL '7 days' THEN 1 END) as last_7d,
  MIN(switched_at) as oldest_record,
  MAX(switched_at) as newest_record
FROM api_key_model_switches;

-- ============================================================================
-- DEBUGGING: Check if rate limiting is actually being checked
-- ============================================================================

-- Look for entries created AFTER enabling rate limiting
-- If table is empty after enabling, the code isn't executing
SELECT 
  COUNT(*) as switches_recorded,
  MIN(switched_at) as first_switch,
  MAX(switched_at) as last_switch
FROM api_key_model_switches
WHERE switched_at >= NOW() - INTERVAL '1 hour';
-- If count > 0, rate limiting is active and recording switches

-- ============================================================================
-- PERFORMANCE CHECK
-- ============================================================================

-- Verify indexes exist
SELECT 
  schemaname,
  tablename,
  indexname,
  indexdef
FROM pg_indexes
WHERE tablename = 'api_key_model_switches';
-- Expected: 2 indexes (ix_api_key_switches_lookup, ix_user_switches_lookup)

-- Check query performance (should be fast)
EXPLAIN ANALYZE
SELECT COUNT(*) 
FROM api_key_model_switches 
WHERE api_key_id = 4 
AND switched_at >= NOW() - INTERVAL '1 hour';
-- Should use index scan, not sequential scan

-- ============================================================================
-- COMPARISON: Before vs After Rate Limiting
-- ============================================================================

-- Compare session creation patterns
-- BEFORE (check current sessions table)
SELECT 
  DATE_TRUNC('hour', created_at) as hour,
  api_key_id,
  COUNT(*) as sessions,
  COUNT(DISTINCT model) as models_switched
FROM sessions
WHERE api_key_id = 4
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY hour, api_key_id
ORDER BY hour DESC;

-- AFTER (check model_switches table after enabling)
SELECT 
  DATE_TRUNC('hour', switched_at) as hour,
  api_key_id,
  COUNT(*) as switches_recorded,
  COUNT(DISTINCT to_model) as unique_models
FROM api_key_model_switches
WHERE api_key_id = 4
  AND switched_at >= NOW() - INTERVAL '24 hours'
GROUP BY hour, api_key_id
ORDER BY hour DESC;

-- Expected: Switch count drops from 150+/hour to 0 (rate limited)
