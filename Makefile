.PHONY: clean distclean archive

# Remove temp/cache/backup files from cppcheck runs
clean:
	rm -rf .cppcheck-cache uninit_report.txt *.bak __pycache__ *.pyc

# Also remove generated test files (regenerate with ./generate_test.sh)
distclean: clean
	rm -f uninit_test.h uninit_test.cpp uninit_guarded_test.h uninit_guarded_test.cpp

# Create a tarball of scripts and docs (excludes .cpp, LICENSE)
archive:
	git archive -o cppcheck-uninit-fixer.tar.gz --prefix=cppcheck-uninit-fixer/ HEAD '*.sh' '*.py' README.md README.html Makefile
