# Comprehensive Testing Guide for Agentic SDLC

## Overview

This guide explains the comprehensive testing system for agentic-sdlc projects, including test case generation, evidence collection (API responses, screenshots), and UI integration.

## Updated Skills

### `sdlc-validate` - Comprehensive Testing Skill

**Location:** `sdlc_orchestrator/skills/sdlc-validate.md`

**What it does:**
1. **Generates comprehensive test cases** with 100% requirement coverage
2. **Executes all tests** and captures evidence (API responses, screenshots, logs)
3. **Creates detailed test results** with pass/fail status and fix recommendations
4. **Updates workflow state** to make artifacts accessible in SDLC UI

**Key Features:**
- ✅ Covers all functional and non-functional requirements
- ✅ Generates positive, negative, and edge case tests
- ✅ Captures real execution evidence (JSON, screenshots, logs)
- ✅ Provides realistic test results with issues and recommendations
- ✅ Integrates with SDLC UI for artifact viewing

## Artifact Storage Structure

### Standard Directory Layout

```
docs/sdlc/<project-slug>/
├── requirements.md          # Project requirements
├── design.md               # Design document
├── plan.md                 # Implementation plan
├── test-cases.md           # Comprehensive test cases (NEW)
├── test-results.md         # Test execution results (NEW)
└── evidence/               # Test evidence artifacts (NEW)
    ├── README.md           # Evidence directory documentation
    ├── TC-001-response.json       # API response samples
    ├── TC-002-screenshot.png      # UI screenshots
    ├── TC-003-logs.txt            # Execution logs
    ├── TC-050-metrics.json        # Performance metrics
    └── coverage-report.html       # Code coverage
```

### File Naming Conventions

- **Test Cases:** `test-cases.md` (standard name for UI access)
- **Test Results:** `test-results.md` (standard name for UI access)
- **Evidence Files:** `TC-XXX-description.ext` where:
  - `TC-XXX` = Test case ID
  - `description` = Brief descriptor
  - `ext` = File extension (.json, .png, .txt)

## Evidence Collection

### 1. API Response Capture

**Purpose:** Capture actual API responses for validation and documentation

**Implementation:**

```python
import json
from pathlib import Path

def capture_api_response(test_id: str, response: dict, project_slug: str):
    """Capture API response as evidence"""
    evidence_dir = Path(f"docs/sdlc/{project_slug}/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    evidence_file = evidence_dir / f"{test_id}-response.json"
    with open(evidence_file, "w") as f:
        json.dump(response, f, indent=2)
    
    print(f"✓ Captured API response: {evidence_file}")
    return evidence_file

# Usage example
response = requests.get("https://api.example.com/users/123")
capture_api_response("TC-001", response.json(), "my-project")
```

**Example Output:**

```json
{
  "status": "success",
  "data": {
    "user_id": "123",
    "username": "testuser",
    "email": "test@example.com"
  },
  "timestamp": "2026-04-22T14:32:18.234Z"
}
```

### 2. Screenshot Capture

**Purpose:** Visual evidence of UI state and user-facing features

**Implementation (Playwright):**

```python
from playwright.sync_api import sync_playwright
from pathlib import Path

def capture_screenshot(test_id: str, url: str, project_slug: str, selector: str = None):
    """Capture screenshot as evidence"""
    evidence_dir = Path(f"docs/sdlc/{project_slug}/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url)
        
        # Optional: wait for specific element
        if selector:
            page.wait_for_selector(selector)
        
        screenshot_file = evidence_dir / f"{test_id}-screenshot.png"
        page.screenshot(path=str(screenshot_file))
        browser.close()
    
    print(f"✓ Captured screenshot: {screenshot_file}")
    return screenshot_file

# Usage example
capture_screenshot(
    "TC-052",
    "http://localhost:3000/projects/my-project/graph",
    "my-project",
    selector="#graph-container"
)
```

**Implementation (Selenium):**

```python
from selenium import webdriver
from pathlib import Path

def capture_screenshot_selenium(test_id: str, url: str, project_slug: str):
    """Capture screenshot using Selenium"""
    evidence_dir = Path(f"docs/sdlc/{project_slug}/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    driver = webdriver.Chrome()
    driver.get(url)
    
    screenshot_file = evidence_dir / f"{test_id}-screenshot.png"
    driver.save_screenshot(str(screenshot_file))
    driver.quit()
    
    print(f"✓ Captured screenshot: {screenshot_file}")
    return screenshot_file
```

### 3. Performance Metrics Capture

**Purpose:** Document performance benchmarks and validate NFRs

**Implementation:**

```python
import time
import json
from pathlib import Path
from statistics import mean, median, stdev

def capture_performance_metrics(test_id: str, project_slug: str, 
                                target_ms: int, executions: int = 3):
    """Capture performance metrics with multiple runs"""
    evidence_dir = Path(f"docs/sdlc/{project_slug}/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    for i in range(executions):
        start = time.time()
        # Execute operation to measure
        execute_test_operation()
        duration_ms = (time.time() - start) * 1000
        
        results.append({
            "attempt": i + 1,
            "duration_ms": round(duration_ms, 2),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        })
    
    durations = [r["duration_ms"] for r in results]
    metrics = {
        "test_id": test_id,
        "target_ms": target_ms,
        "actual_ms": round(mean(durations), 2),
        "status": "PASS" if mean(durations) <= target_ms else "FAIL",
        "executions": results,
        "statistics": {
            "mean_ms": round(mean(durations), 2),
            "median_ms": round(median(durations), 2),
            "min_ms": round(min(durations), 2),
            "max_ms": round(max(durations), 2),
            "std_dev_ms": round(stdev(durations), 2) if len(durations) > 1 else 0
        }
    }
    
    metrics_file = evidence_dir / f"{test_id}-metrics.json"
    with open(metrics_file, "w") as f:
        json.dump(metrics, f, indent=2)
    
    print(f"✓ Captured performance metrics: {metrics_file}")
    return metrics_file

# Usage example
capture_performance_metrics("TC-050", "my-project", target_ms=500, executions=5)
```

### 4. Log Capture

**Purpose:** Capture execution logs, error traces, system output

**Implementation:**

```python
import logging
from pathlib import Path

def capture_logs(test_id: str, project_slug: str, log_content: str):
    """Capture logs as evidence"""
    evidence_dir = Path(f"docs/sdlc/{project_slug}/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = evidence_dir / f"{test_id}-logs.txt"
    with open(log_file, "w") as f:
        f.write(log_content)
    
    print(f"✓ Captured logs: {log_file}")
    return log_file

# Usage with Python logging
import io
log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
logger = logging.getLogger("test")
logger.addHandler(handler)

# Run test
try:
    execute_test()
except Exception as e:
    logger.exception("Test failed")

# Capture logs
capture_logs("TC-008", "my-project", log_stream.getvalue())
```

## UI Integration

### Making Artifacts Accessible in SDLC UI

The SDLC UI automatically displays test artifacts when they're properly registered in the workflow state.

#### Step 1: Store Artifacts in Standard Location

```bash
docs/sdlc/<project-slug>/
├── test-cases.md       # Must use this exact name
├── test-results.md     # Must use this exact name
└── evidence/           # Must use this directory name
```

#### Step 2: Update Workflow State

```python
from sdlc_orchestrator.state_machine import WorkflowState

# In your testing skill
wf = WorkflowState(".")  # Current project directory
wf.mark_artifact("test_cases", "docs/sdlc/my-project/test-cases.md")
wf.mark_artifact("test_results", "docs/sdlc/my-project/test-results.md")
```

#### Step 3: Verify UI Access

1. Start the SDLC dashboard: `sdlc ui`
2. Navigate to your project
3. Click on artifact links in the project card
4. Test artifacts should open in the beautiful HTML viewer

### Artifact Viewer Features

The updated artifact viewer (`sdlc_orchestrator/ui/artifact_viewer.html`) provides:

- ✅ **Markdown rendering** with syntax highlighting
- ✅ **JSON beautification** with syntax coloring
- ✅ **Screenshot display** with click-to-enlarge
- ✅ **Test case cards** with pass/fail badges
- ✅ **Responsive design** for mobile and desktop
- ✅ **Direct evidence links** from test results to evidence files

## Complete Testing Workflow

### Step-by-Step Process

1. **Generate Comprehensive Test Cases**
   ```bash
   sdlc orchestrate  # Runs sdlc-validate skill during testing phase
   ```
   
   Or manually invoke:
   ```bash
   kiro-cli chat "Run /sdlc-validate for current project"
   ```

2. **Execute Tests and Capture Evidence**
   - Skill automatically runs test suites
   - Captures API responses to `evidence/TC-XXX-response.json`
   - Takes screenshots to `evidence/TC-XXX-screenshot.png`
   - Records performance metrics to `evidence/TC-XXX-metrics.json`

3. **Generate Test Results**
   - Skill creates `test-results.md` with all execution details
   - Links to evidence files from test results
   - Documents issues with root cause and fix recommendations

4. **Update Workflow State**
   - Skill registers artifacts in state.json
   - Makes artifacts accessible in UI

5. **View in UI**
   - Open dashboard: `sdlc ui`
   - Click test artifact links
   - Beautiful HTML rendering with evidence display

## Example Test Result with Evidence

```markdown
### ✅ TC-004: YouTube URL Input
- **Status:** PASS
- **Execution Time:** 8.7s
- **Result:** YouTube video downloaded successfully
- **Evidence:** [API Response](evidence/TC-004-youtube-response.json)
- **API Response Sample:**
\`\`\`json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Sample YouTube Video",
  "duration": 212,
  "status": "downloaded"
}
\`\`\`

### ❌ TC-073: Authorization Check
- **Status:** FAIL
- **Execution Time:** 1.2s
- **Expected:** 403 Forbidden for unauthorized access
- **Actual:** 200 OK (authorization bypassed)
- **Evidence:** 
  - [Request logs](evidence/TC-073-logs.txt)
  - [Screenshot](evidence/TC-073-screenshot.png)
- **Issue:** Role check bypassable via API race condition
- **Root Cause:** Missing mutex lock in authorization middleware
- **Fix:** Add distributed lock for role validation
- **Priority:** P0 - Critical
```

## Best Practices

### Test Case Quality

- ✅ **100% requirement coverage** - Every FR and NFR must be tested
- ✅ **3+ test cases per requirement** - Positive, negative, edge cases
- ✅ **Clear acceptance criteria** - Specific, measurable, verifiable
- ✅ **Realistic test data** - Use production-like data (sanitized)

### Evidence Quality

- ✅ **Actual execution results** - No mocked or theoretical evidence
- ✅ **Complete API responses** - Include full response structure
- ✅ **Clear screenshots** - Highlight relevant UI elements
- ✅ **Timestamped logs** - Include execution timestamps
- ✅ **Sanitized data** - Remove credentials, PII, sensitive info

### Documentation Quality

- ✅ **Pass/Fail with evidence** - Every test must have evidence
- ✅ **Root cause analysis** - Explain WHY failures occurred
- ✅ **Fix recommendations** - Provide actionable next steps
- ✅ **Priority assignment** - P0/P1/P2 for issue severity
- ✅ **Realistic estimates** - Provide fix ETAs

## Troubleshooting

### Test Artifacts Not Showing in UI

**Problem:** Test cases/results not visible in dashboard

**Solutions:**
1. Verify files are named exactly `test-cases.md` and `test-results.md`
2. Check artifact paths in state.json are correct
3. Ensure files are in `docs/sdlc/<project-slug>/` directory
4. Refresh dashboard (artifacts update every 30s)

### Evidence Files Not Accessible

**Problem:** Evidence links return 404

**Solutions:**
1. Verify evidence files are in `evidence/` subdirectory
2. Check file names match links in test-results.md
3. Ensure relative links are correct: `evidence/TC-XXX-file.ext`
4. Files must be committed to git

### Screenshot Capture Fails

**Problem:** Screenshot tool errors or blank images

**Solutions:**
1. Install Playwright: `pip install playwright && playwright install`
2. Ensure headless mode works: `playwright install chromium`
3. Add wait for page load: `page.wait_for_load_state("networkidle")`
4. Check URL is accessible from test environment

## Migration Guide

### Updating Existing Projects

To add comprehensive testing to existing projects:

```bash
# 1. Navigate to project
cd /path/to/project

# 2. Run comprehensive testing
kiro-cli chat "Run /sdlc-validate to generate comprehensive test cases and results"

# 3. Verify artifacts created
ls docs/sdlc/<project-slug>/test-cases.md
ls docs/sdlc/<project-slug>/test-results.md
ls docs/sdlc/<project-slug>/evidence/

# 4. Check UI
sdlc ui  # Open dashboard and verify artifacts visible
```

## Summary

The updated `sdlc-validate` skill provides:

1. ✅ **Comprehensive test case generation** (100% requirement coverage)
2. ✅ **Real evidence capture** (API responses, screenshots, logs, metrics)
3. ✅ **Detailed test results** with issues and recommendations
4. ✅ **UI integration** for beautiful artifact viewing
5. ✅ **Standardized structure** for consistent quality across projects

**All test artifacts are automatically accessible in the SDLC UI for easy review and collaboration.**

---

For questions or improvements, see `/Users/rsharma/projects/agentic-sdlc/README.md`
