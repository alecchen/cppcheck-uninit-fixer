.PHONY: clean distclean archive check check-all

# Remove temp/cache/backup files from cppcheck runs
clean:
	rm -rf .cppcheck-cache uninit_report.txt *.bak __pycache__ *.pyc

# Also remove generated test files (regenerate with ./generate_test.sh)
distclean: clean
	rm -f uninit_test.h uninit_test.cpp uninit_guarded_test.h uninit_guarded_test.cpp

# Create a tarball of scripts and docs (excludes .cpp, LICENSE)
archive:
	git archive -o cppcheck-uninit-fixer.tar.gz --prefix=cppcheck-uninit-fixer/ HEAD '*.sh' '*.py' README.md README.html Makefile

# Quick check: guard chain tracking
check:
	python3 test_elif_guards.py

# Full checks: guard tracking + -I/--project integration
check-all: check
	python3 test_bear_integration.py
