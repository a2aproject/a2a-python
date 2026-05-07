#!/bin/bash
# Local replica of .github/workflows/linter.yaml (excluding jscpd copy-paste check)

# ANSI color codes for premium output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

FAILED=0

echo -e "${BLUE}${BOLD}=== A2A Python Fixed-and-Lint Suite ===${NC}"
echo -e "Fixing formatting and linting issues, then verifying types...\n"

# 1. Ruff Linter (with fix)
echo -e "${YELLOW}${BOLD}--- [1/3] Running Ruff Linter (fix) ---${NC}"
if uv run ruff check --fix; then
    echo -e "${GREEN}✓ Ruff Linter passed (and fixed what it could)${NC}"
else
    echo -e "${RED}✗ Ruff Linter failed${NC}"
    FAILED=1
fi

# 2. Ruff Formatter
echo -e "\n${YELLOW}${BOLD}--- [2/3] Running Ruff Formatter (apply) ---${NC}"
if uv run ruff format; then
    echo -e "${GREEN}✓ Ruff Formatter applied${NC}"
else
    echo -e "${RED}✗ Ruff Formatter failed${NC}"
    FAILED=1
fi

# 3. ty Type Checker
echo -e "\n${YELLOW}${BOLD}--- [3/3] Running ty Type Checker ---${NC}"
if uv run ty check; then
    echo -e "${GREEN}✓ ty passed${NC}"
else
    echo -e "${RED}✗ ty failed${NC}"
    FAILED=1
fi

echo -e "\n${BLUE}${BOLD}=========================================${NC}"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}${BOLD}SUCCESS: All linting and formatting tasks complete!${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}FAILURE: One or more steps failed.${NC}"
    exit 1
fi
