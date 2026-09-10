#!/bin/sh
# generate_test.sh -- create fresh test files with uninitialized members
# Run this before testing to reset:
#   ./generate_test.sh
#   ./cppcheck-fix-uninit.py --report-only uninit_test.cpp

set -e

# ---- Header (member declarations -- always clean) ----
cat > uninit_test.h << 'HEADER'
#ifndef UNINIT_TEST_H
#define UNINIT_TEST_H

#include <string>
#include <cstdint>

class DataHolder {
public:
    DataHolder();
    DataHolder(int a, double b);
    explicit DataHolder(const char* s);
    ~DataHolder() = default;

    int getId() const { return id_; }
    double getValue() const { return value_; }
    float getRatio() const { return ratio_; }
    bool getFlag() const { return flag_; }
    char getCode() const { return code_; }
    short getCount() const { return count_; }
    long getBig() const { return big_; }
    unsigned getMask() const { return mask_; }
    const char* getLabel() const { return label_; }
    int* getBuf() const { return buf_; }
    int  getBufSize() const { return buf_size_; }
    std::string getName() const { return name_; }
    // const / volatile / fixed-width
    int getConstVal() const { return const_val_; }
    double getConstRef() const { return const_ref_; }
    const int* getConstPtr() const { return const_ptr_; }
    const char* const getConstString() const { return const_string_; }
    int* const getConstBuf() const { return const_buf_; }
    uint32_t getU32() const { return u32_; }
    uint64_t getU64() const { return u64_; }
    int32_t getI32() const { return i32_; }
    int64_t getI64() const { return i64_; }
    size_t getSize() const { return sz_; }
    int getVol() const { return vol_; }
    int getCv() const { return cv_; }
    volatile int getVolCopy() const { return vol_; }
    uint32_t getConstU32() const { return const_u32_; }
    const volatile int getCvCopy() const { return cv_; }

private:
    int      id_;
    double   value_;
    float    ratio_;
    bool     flag_;
    char     code_;
    short    count_;
    long     big_;
    unsigned mask_;
    const char* label_;
    int*     buf_;
    int      buf_size_;
    std::string name_;
    // const / reference
    const int const_val_;
    const double& const_ref_;
    const int* const_ptr_;
    const char* const const_string_;
    int* const const_buf_;
    // fixed-width integer types
    uint32_t u32_;
    uint64_t u64_;
    int32_t i32_;
    int64_t i64_;
    size_t  sz_;
    // volatile
    volatile int vol_;
    const volatile int cv_;
    // const fixed-width
    const uint32_t const_u32_;
    // volatile pointer variants
    volatile int* vol_ptr_;
    int* volatile ptr_vol_;
    const volatile int* cv_ptr_;
    int* const volatile ptr_cv_;
};

#endif // UNINIT_TEST_H
HEADER

# ---- Source (constructors with MISSING initializers) ----
cat > uninit_test.cpp << 'SOURCE'
#include "uninit_test.h"
#include <cstring>

// Default ctor -- only const_ref_ initialized (compiler-mandated)
// Everything else is UNINITIALIZED -- 27 members missing
DataHolder::DataHolder()
    : const_ref_(value_)
{}

// Param ctor -- init-list has some, misses 15 members
DataHolder::DataHolder(int a, double b)
    : id_(a), value_(b), const_val_(a), const_ref_(value_),
      const_ptr_(nullptr), const_string_("d"), const_buf_(nullptr),
      const_u32_(0), vol_ptr_(nullptr), ptr_vol_(nullptr),
      cv_ptr_(nullptr), ptr_cv_(nullptr), ratio_(static_cast<float>(b))
{}

// String ctor -- init-list has const/ref/ptr, body assigns label_
// Misses 15 members
DataHolder::DataHolder(const char* s)
    : id_(42), buf_size_(static_cast<int>(std::strlen(s))),
      const_val_(0), const_ref_(value_), const_ptr_(nullptr),
      const_string_(s), const_buf_(nullptr), const_u32_(0),
      vol_ptr_(nullptr), ptr_vol_(nullptr), cv_ptr_(nullptr),
      ptr_cv_(nullptr)
{
    label_ = s;
}
SOURCE

# ---- Guarded test header (with #if guards) ----
cat > uninit_guarded_test.h << 'GUARDED_H'
#ifndef UNINIT_GUARDED_TEST_H
#define UNINIT_GUARDED_TEST_H

#include <string>
#include <cstdint>

// ====================================================================
// Guarded test: multiple #if flags + compound conditions.
//
// Flags used (all UNDEFINED -- cppcheck must test all combos):
//   USE_LEGACY      -- enables legacy code paths
//   FEATURE_X       -- feature toggle
//   FEATURE_Y       -- feature toggle (appears with FEATURE_X)
//   PLATFORM_A       -- platform-specific code
//   PLATFORM_B       -- another platform
//   BUILD_PERFORMANCE -- extra optimization fields
// ====================================================================

class ServiceConfig {
public:
    ServiceConfig();
    ServiceConfig(int version);
    explicit ServiceConfig(const char* host);

    int getVersion() const { return version_; }
    double getTimeout() const { return timeout_; }
    const char* getHost() const { return host_; }
    int getRetries() const { return retries_; }

#if USE_LEGACY
    int getLegacyPort() const { return legacy_port_; }
    const char* getLegacyPath() const { return legacy_path_; }
#else
    int getModernPort() const { return modern_port_; }
#endif

#if FEATURE_X && FEATURE_Y
    float getRatio() const { return x_y_ratio_; }
#endif

#if PLATFORM_A || PLATFORM_B
    int getPlatformId() const { return platform_id_; }
#endif

#if BUILD_PERFORMANCE
    int getCacheSize() const { return cache_size_; }
#endif

private:
    int version_;
    double timeout_;
    const char* host_;
    int retries_;

#if USE_LEGACY
    int legacy_port_;
    const char* legacy_path_;
#else
    int modern_port_;
#endif

#if FEATURE_X && FEATURE_Y
    float x_y_ratio_;
#endif

#if PLATFORM_A || PLATFORM_B
    int platform_id_;
#endif

#if BUILD_PERFORMANCE
    int cache_size_;
#endif
};

#if USE_LEGACY
class LegacyHandler {
public:
    LegacyHandler();
    int getBufferSize() const { return buf_size_; }
    const char* getPath() const { return path_; }
    bool getEnabled() const { return enabled_; }
private:
    int buf_size_;
    const char* path_;
    bool enabled_;
};
#endif

#if !USE_LEGACY
class ModernHandler {
public:
    ModernHandler();
    int getPort() const { return port_; }
    int getTimeoutMs() const { return timeout_ms_; }
    const char* getEndpoint() const { return endpoint_; }
private:
    int port_;
    int timeout_ms_;
    const char* endpoint_;
};
#endif

#endif // UNINIT_GUARDED_TEST_H
GUARDED_H

# ---- Guarded test source (constructors with MISSING initializers) ----
cat > uninit_guarded_test.cpp << 'GUARDED_CPP'
#include "uninit_guarded_test.h"
#include <cstring>

// ====================================================================
// Constructor bodies use #if LABEL blocks to conditionally init members.
// Some members are left uninitialized in ALL paths; others are init'd
// only in certain flag configurations.
// ====================================================================

// ---- ServiceConfig default ctor ----
ServiceConfig::ServiceConfig()
    : host_(nullptr)
    , retries_(0)
    , timeout_(0.0)
{
    version_ = 1;

#if USE_LEGACY
    legacy_port_ = 8080;
    legacy_path_ = "/legacy";
#else
    modern_port_ = 9090;
    timeout_ = 30.0;
#endif

#if FEATURE_X && FEATURE_Y
    x_y_ratio_ = 1.5f;
#endif

#if PLATFORM_A || PLATFORM_B
    platform_id_ = 100;
#endif

#if BUILD_PERFORMANCE
    cache_size_ = 4096;
#endif
}

// ---- ServiceConfig version ctor ----
ServiceConfig::ServiceConfig(int version)
{
    version_ = version;
    timeout_ = 60.0;
    host_ = "default";

#if USE_LEGACY
    legacy_port_ = 8080;
    legacy_path_ = "/legacy";
#else
    modern_port_ = 9090;
#endif
}

// ---- ServiceConfig string ctor ----
ServiceConfig::ServiceConfig(const char* host)
{
    host_ = host;

#if USE_LEGACY
    legacy_port_ = 8080;
    legacy_path_ = "/legacy";
#else
    modern_port_ = 9090;
#endif

#if PLATFORM_A || PLATFORM_B
#endif
}

// ---- LegacyHandler ----
#if USE_LEGACY
LegacyHandler::LegacyHandler()
{
}
#endif

// ---- ModernHandler ----
#if !USE_LEGACY
ModernHandler::ModernHandler()
{
    port_ = 443;
}
#endif
GUARDED_CPP

echo "Generated uninit_test.h + uninit_test.cpp"
echo "57 uninitialized members across 3 constructors"
echo ""
echo "Generated uninit_guarded_test.h + uninit_guarded_test.cpp"
echo "15 uninitialized members with 6 #if flags (compound + single)"
echo ""
echo "  # Quick tests (default: in-class initializers in headers):"
echo "  ./check_uninit_all.sh uninit_test.cpp"
echo "  ./cppcheck-fix-uninit.py --report-only uninit_test.cpp"
echo "  ./cppcheck-fix-uninit.py uninit_test.cpp"
echo ""
echo "  # Guarded tests (with #if guards):"
echo "  ./check_uninit_all.sh uninit_guarded_test.cpp"
echo "  ./cppcheck-fix-uninit.py -v --report-only uninit_guarded_test.cpp"
echo "  ./cppcheck-fix-uninit.py uninit_guarded_test.cpp"
echo ""
echo "  # Both together:"
echo "  ./cppcheck-fix-uninit.py uninit_test.cpp uninit_guarded_test.cpp"
echo ""
echo "  # Init-list mode (opt-in, skips guarded members):"
echo "  ./cppcheck-fix-uninit.py --init-list uninit_test.cpp"
echo ""
echo "  # Re-generate and repeat:"
echo "  ./generate_test.sh"
