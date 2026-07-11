# BAN/Identifier Masking Audit Report

**Date**: 2026-07-11
**Auditor**: Mistral Vibe (via ha-integration-dev execution)
**Scope**: All custom_components/engie_be/*.py files
**Plan**: 103
**Status**: COMPLETE

---

## Executive Summary

Audit of all log statements in the ENGIE Belgium integration codebase to verify complete masking coverage of sensitive identifiers (BAN, premises_number, EAN).

**Total files audited**: 11 Python files in custom_components/engie_be/
**Total log statements examined**: 100+
**Log statements with identifiers**: 30+
**Properly masked**: 28+
**Requiring fixes**: 2

---

## Audit Methodology

1. Identified all files containing LOGGER statements
2. For each file, searched for occurrences of `business_agreement_number`, `premises_number`, `ean`
3. Checked if these identifiers appear in log statements without `mask_identifier()` or `_hash_ean()`
4. Verified test files use `000000000000` placeholder consistently

---

## Findings

### ✅ Properly Masked (No Action Required)

#### coordinator.py
- **Lines 408-414**: `mask_identifier(business_agreement_number)` in warning about feature flags
- **Lines 419-424**: `mask_identifier(business_agreement_number)` in debug about Happy Hours enrolment
- **Lines 468-472**: `mask_identifier(business_agreement_number)` in warning about price flags
- **Lines 477-481**: `mask_identifier(business_agreement_number)` in debug about slot duration
- **Lines 506-512**: `mask_identifier(business_agreement_number)` in debug about peek slots
- **Lines 513-520**: `mask_identifier(business_agreement_number)` in warning about epex_p60
- **Lines 526-531**: `mask_identifier(business_agreement_number)` in info about epex_p60
- **Lines 611-614**: `mask_identifier(ean)` in debug about energy insights
- **Lines 669-673**: `mask_identifier(business_agreement_number)` in debug about service points
- **Lines 683-688**: `mask_identifier(self.business_agreement_number)` in info about slots
- **Lines 781-788**: `mask_identifier(business_agreement_number)` in debug about happy hour events
- **Lines 787-793**: `mask_identifier(business_agreement_number)` in warning about fetch
- **Lines 798-804**: `mask_identifier(business_agreement_number)` in info about result
- **Lines 839-845**: `mask_identifier(business_agreement_number)` in debug about service points
- **Lines 845-851**: `mask_identifier(business_agreement_number)` in warning about None data
- **Lines 856-862**: `mask_identifier(business_agreement_number)` in info about counts
- **Lines 889-896**: `mask_identifier(business_agreement_number)` in warning about consent
- **Lines 905-914**: `mask_identifier(business_agreement_number)` in debug about contract
- **Lines 914-919**: `mask_identifier(business_agreement_number)` in debug about happy hour flags
- **Lines 971-978**: `mask_identifier(business_agreement_number)` in warning about fetch
- **Lines 990-996**: `mask_identifier(business_agreement_number)` in debug about happy hour
- **Lines 1010-1015**: `mask_identifier(business_agreement_number)` in debug about happy hour fetch
- **Lines 1029-1034**: `mask_identifier(business_agreement_number)` in debug about happy hour options
- **Lines 1043-1050**: `mask_identifier(business_agreement_number)` in debug about flags
- **Lines 1108-1114**: `mask_identifier(business_agreement_number)` in debug about service points
- **Lines 1119-1125**: `mask_identifier(business_agreement_number)` in debug about missing endpoints

#### sensor.py
- **Lines 270-276**: `mask_identifier(coordinator.business_agreement_number)` in debug about Happy Hours enrolment
- **Lines 282-287**: `mask_identifier(coordinator.business_agreement_number)` in debug about not enrolled

#### binary_sensor.py
- **Lines 153-154**: `mask_identifier(sub_data.coordinator.business_agreement_number)` in device_info
- **Lines 165-166**: `mask_identifier(sub_data.coordinator.business_agreement_number)` in device_info

#### entity.py
- **Line 82**: `mask_identifier(ban)` in boundary logging

#### _statistics.py
- **Lines 417-421**: `***%s` with `masked_ban` (last 4 chars) in info about historical import
- **Lines 422-425**: `***%s` with `masked_ban` in debug about Happy Hours
- **Lines 611-615**: `***%s` with `masked_ban` in debug about imported chunk

#### api.py
- **Lines 310-314**: `_redact_text(self.refresh_token)` in debug about token rotation

#### _api_logging.py
- **Infrastructure**: `_redact_text` function and `_PARTIAL_MASK_BODY_KEYS` include `businessagreementnumber`, `premisesnumber`, `ean`

#### __init__.py
- **Line 1006**: `_hash_ean(ean)` in debug about service-point division

---

### ⚠️ Requires Attention (Low Risk)

#### 1. trigger.py:553 - Entity ID contains BAN

**File**: `custom_components/engie_be/trigger.py`  
**Line**: 553  
**Code**:
```python
LOGGER.debug("Failed to fetch events from %s: %s", entity_id, exc)
```

**Issue**: `entity_id` is in format `calendar.engie_belgium_{BAN}` where BAN is the full business agreement number. Logging the entity_id exposes the full BAN.

**Evidence**: 
- `calendar.py:150`: `self.entity_id = f"calendar.engie_belgium_{ban}"`
- `ban` = `subentry.data.get(CONF_BUSINESS_AGREEMENT_NUMBER)` (line 148)

**Risk**: LOW - Only appears in DEBUG-level logging, but still exposes PII in debug logs

**Fix**: Use `mask_identifier` on the BAN part of the entity_id:
```python
# Extract BAN from entity_id and mask it
# entity_id format: calendar.engie_belgium_{BAN}[_suffix]
# or extract BAN from the entity_id
from .api import mask_identifier
# ...
ban_from_entity = entity_id.split("engie_belgium_")[-1].split("_")[0]
LOGGER.debug("Failed to fetch events from %s: %s", f"calendar.engie_belgium_{mask_identifier(ban_from_entity)}", exc)
```

Or simpler, just mask the entire entity_id:
```python
LOGGER.debug("Failed to fetch events from %s: %s", mask_identifier(entity_id), exc)
```

**Note**: This is DEBUG level, so impact is limited to users running with debug logging enabled.

---

#### 2. _statistics.py:415 - Device name contains unmasked BAN

**File**: `custom_components/engie_be/_statistics.py`  
**Line**: 415  
**Code**:
```python
device_name = subentry.title or f"BAN {business_agreement_number}"
```

**Issue**: `device_name` variable contains the full BAN. This is then used in:
- Line 259: `name=f"Historical {spec.display_name} - {device_name}"` (statistic metadata)
- Line 606: `_metadata(business_agreement_number, stream, device_name)`

**Risk**: LOW - Not directly logged, but used in metadata. The metadata name could potentially appear in logs or error messages.

**Fix**: Use masked BAN in device_name:
```python
device_name = subentry.title or f"BAN {mask_identifier(business_agreement_number)}"
```

**Note**: This is already partially mitigated by lines 414 and 417-421 which use `masked_ban = business_agreement_number[-4:]` and log with `***%s`. However, the full BAN in device_name could still leak through other code paths.

---

### ✅ No Issues Found

The following files were audited and found to have no unmasked identifier logging:

- **api.py**: All token logging uses `_redact_text()`
- **calendar.py**: Only logs subentry_id, not BAN directly
- **config_flow.py**: Only logs exceptions, not identifiers directly
- **_happy_hour.py**: Only logs timestamps and parsing errors, not identifiers
- **store.py**: Only logs subentry_id and counts, not identifiers
- **entity.py**: Properly uses `mask_identifier()` in boundary logging
- **binary_sensor.py**: Properly uses `mask_identifier()` in device_info

---

## Test File Audit

### ✅ All Tests Use Placeholder

**Placeholder**: `000000000000`

**Files verified**:
- `tests/test_api_billing.py`: Line 13
- `tests/test_api_peaks.py`: Line 18
- `tests/test_api_solar_surplus.py`: Line 14
- `tests/test_api_tou_schedules.py`: Line 18
- `tests/test_binary_sensor_tou.py`: Line 57
- `tests/test_condition.py`: Line 61
- `tests/test_init.py`: Line 1628
- `tests/test_sensor_billing.py`: Line 49
- `tests/test_sensor_solar_surplus.py`: Line 58
- `tests/test_sensor_solar_surplus_schedulers.py`: Line 77
- `tests/test_sensor_tou.py`: Line 58
- `tests/test_statistics.py`: Multiple uses (lines 42, 43, 241, 287-290, etc.)
- `tests/test_trigger.py`: Line 96

**Verdict**: All test files consistently use `000000000000` as the placeholder BAN. No real BAN values found in tests.

---

## Summary Statistics

| Category | Count |
|----------|-------|
| Files audited | 11 |
| Total log statements | 100+ |
| Log statements with identifiers | 30+ |
| Properly masked | 28+ |
| **Requiring fixes** | **2** |
| Test files using placeholder | 12+ |

---

## Recommendations

### Immediate Actions (P1)

1. **Fix trigger.py:553** - Mask entity_id before logging (or extract and mask BAN from entity_id)
2. **Fix _statistics.py:415** - Use masked BAN in device_name

### Long-term Improvements (P2)

1. **Consider adding a linter rule** to catch unmasked identifier logging:
   - Pattern: `LOGGER.*%s.*business_agreement_number` without `mask_identifier`
   - Pattern: `LOGGER.*%s.*premises_number` without `mask_identifier`
   - Pattern: `LOGGER.*%s.*ean` without `mask_identifier` or `_hash_ean`

2. **Document masking policy** in CONTRIBUTING.md or developer documentation

3. **Add pre-commit hook** to check for unmasked identifiers in log statements

---

## Verification Commands

Run these commands to verify the fixes:

```bash
# Check for unmasked BAN in logs (excluding masked usage)
grep -rn "business_agreement_number" custom_components/engie_be/*.py \
  | grep -i "LOGGER\\|logging" \
  | grep -v "mask_identifier" \
  | grep -v "_hash_ean"

# Check for unmasked premises_number in logs
grep -rn "premises_number" custom_components/engie_be/*.py \
  | grep -i "LOGGER\\|logging" \
  | grep -v "mask_identifier"

# Check for unmasked EAN in logs
grep -rnE "\\bean\\b" custom_components/engie_be/*.py \
  | grep -i "LOGGER\\|logging" \
  | grep -v "mask_identifier" \
  | grep -v "_hash_ean"

# Verify all tests use placeholder
grep -rn "business_agreement_number.*=" tests/ \
  | grep -v "000000000000" \
  | grep -v "CONF_BUSINESS_AGREEMENT_NUMBER"
```

---

## Conclusion

The audit found **2 low-risk issues** that should be fixed to ensure complete PII protection in logs:

1. **trigger.py:553** - Entity ID logging exposes BAN
2. **_statistics.py:415** - Device name contains unmasked BAN

All other log statements properly mask sensitive identifiers using `mask_identifier()` or `_hash_ean()`.

Test files consistently use the `000000000000` placeholder, which is excellent practice.

**Overall Assessment**: ✅ GOOD - Minor fixes needed for complete coverage

---

## Related Files

- `custom_components/engie_be/_api_logging.py`: Redaction infrastructure
- `custom_components/engie_be/api.py:143`: `mask_identifier` export
- `custom_components/engie_be/diagnostics.py`: `_hash_ean` function
- `docs/audit-2026-07-11-continued.md`: Original SEC-03 finding
- `docs/audit-reconciliation-2026-07-11.md`: Current reconciliation state
- `plans/103-ban-masking-audit.md`: This audit plan
