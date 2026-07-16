.PHONY: clean distclean

# Remove temp/cache/backup files from cppcheck runs
clean:
	rm -rf .cppcheck-cache uninit_report.txt *.bak __pycache__ *.pyc

# Also remove generated test files (regenerate with ./generate_test.sh)
distclean: clean
	rm -f uninit_test.h uninit_test.cpp uninit_guarded_test.h uninit_guarded_test.cpp
