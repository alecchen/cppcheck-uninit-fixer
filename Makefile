.PHONY: clean distclean archive check check-all html

# Remove temp/cache/backup files from cppcheck runs
clean:
	rm -rf .cppcheck-cache uninit_report.txt *.bak __pycache__ *.pyc

# Also remove generated test files (regenerate with ./generate_test.sh)
distclean: clean
	rm -f uninit_test.h uninit_test.cpp uninit_guarded_test.h uninit_guarded_test.cpp

# Create a tarball of scripts and docs (excludes .cpp, LICENSE)
archive:
	git archive -o cppcheck-uninit-fixer.tar.gz --prefix=cppcheck-uninit-fixer/ HEAD '*.sh' '*.py' README.md README.html Makefile

# Convert README.md → README.html (GitHub style, code highlighting)
html:
	rm -f README.html
	cd markdown-to-html-github-style && node convert.js "cppcheck Uninit Member Detector" "" ../README.md

# Quick check: guard chain tracking + shell script tests + Python fixer tests
check:
	python3 test_elif_guards.py
	python3 test_fast_mode.py
	python3 test_check_uninit_all.py
	python3 test_fix_uninit.py

# Full checks: guard tracking + -I/--project integration
check-all: check
	python3 test_bear_integration.py
