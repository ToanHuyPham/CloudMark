#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <omp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define BENCHMARK_VERSION "1.0"

static double monotonic_seconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) {
        perror("clock_gettime");
        exit(3);
    }
    return (double)value.tv_sec + (double)value.tv_nsec / 1000000000.0;
}

static void usage(const char *program) {
    fprintf(stderr, "Usage: %s --kernel read|write|copy|triad --bytes N --seconds N --threads N\n", program);
}

static int parse_positive_int(const char *value, const char *name) {
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (!value[0] || !end || *end || parsed < 1 || parsed > 65536) {
        fprintf(stderr, "Invalid %s: %s\n", name, value);
        exit(2);
    }
    return (int)parsed;
}

static size_t parse_bytes(const char *value) {
    char *end = NULL;
    errno = 0;
    unsigned long long parsed = strtoull(value, &end, 10);
    if (errno || !value[0] || !end || *end || parsed < 8388608ULL || parsed > 2147483648ULL) {
        fprintf(stderr, "Invalid array size: %s\n", value);
        exit(2);
    }
    return (size_t)parsed;
}

static double *allocate_array(size_t bytes) {
    void *memory = NULL;
    if (posix_memalign(&memory, 64, bytes) != 0) {
        return NULL;
    }
    return (double *)memory;
}

int main(int argc, char **argv) {
    const char *kernel = NULL;
    size_t array_bytes = 0;
    int seconds = 0;
    int threads = 0;
    for (int index = 1; index < argc; index++) {
        if (!strcmp(argv[index], "--kernel") && index + 1 < argc) {
            kernel = argv[++index];
        } else if (!strcmp(argv[index], "--bytes") && index + 1 < argc) {
            array_bytes = parse_bytes(argv[++index]);
        } else if (!strcmp(argv[index], "--seconds") && index + 1 < argc) {
            seconds = parse_positive_int(argv[++index], "duration");
        } else if (!strcmp(argv[index], "--threads") && index + 1 < argc) {
            threads = parse_positive_int(argv[++index], "thread count");
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    if (!kernel || !array_bytes || !seconds || !threads ||
        (strcmp(kernel, "read") && strcmp(kernel, "write") && strcmp(kernel, "copy") && strcmp(kernel, "triad"))) {
        usage(argv[0]);
        return 2;
    }

    array_bytes = (array_bytes / 64U) * 64U;
    size_t count = array_bytes / sizeof(double);
    double *a = allocate_array(array_bytes);
    double *b = allocate_array(array_bytes);
    double *c = allocate_array(array_bytes);
    if (!a || !b || !c) {
        fprintf(stderr, "Unable to allocate three arrays of %zu bytes each.\n", array_bytes);
        free(a);
        free(b);
        free(c);
        return 4;
    }

    omp_set_dynamic(0);
    omp_set_num_threads(threads);
#pragma omp parallel for schedule(static)
    for (size_t index = 0; index < count; index++) {
        a[index] = 1.0 + (double)(index % 97U) / 97.0;
        b[index] = 2.0 + (double)(index % 89U) / 89.0;
        c[index] = 0.5 + (double)(index % 83U) / 83.0;
    }

    const double scalar = 1.00000011920928955078125;
    double checksum = 0.0;
    uint64_t iterations = 0;
    double started = monotonic_seconds();
    double elapsed = 0.0;
    do {
        if (!strcmp(kernel, "read")) {
            double iteration_sum = 0.0;
#pragma omp parallel for reduction(+ : iteration_sum) schedule(static)
            for (size_t index = 0; index < count; index++) {
                iteration_sum += a[index];
            }
            checksum += iteration_sum;
        } else if (!strcmp(kernel, "write")) {
#pragma omp parallel for schedule(static)
            for (size_t index = 0; index < count; index++) {
                a[index] = scalar + (double)(iterations & 7U);
            }
        } else if (!strcmp(kernel, "copy")) {
#pragma omp parallel for schedule(static)
            for (size_t index = 0; index < count; index++) {
                b[index] = a[index];
            }
        } else {
#pragma omp parallel for schedule(static)
            for (size_t index = 0; index < count; index++) {
                a[index] = b[index] + scalar * c[index];
            }
        }
        iterations++;
        elapsed = monotonic_seconds() - started;
    } while (elapsed < (double)seconds);

    if (strcmp(kernel, "read")) {
        checksum = a[0] + a[count / 2U] + a[count - 1U] + b[0] + c[0];
    }
    int streams = !strcmp(kernel, "copy") ? 2 : (!strcmp(kernel, "triad") ? 3 : 1);
    long double processed = (long double)iterations * (long double)array_bytes * (long double)streams;
    long double bandwidth = processed / (long double)elapsed;
    printf(
        "{\"benchmark_version\":\"%s\",\"kernel\":\"%s\",\"threads\":%d,"
        "\"array_bytes\":%zu,\"allocated_bytes\":%zu,\"iterations\":%" PRIu64 ","
        "\"elapsed_seconds\":%.6f,\"bytes_processed\":%.0Lf,"
        "\"bandwidth_bytes_per_second\":%.3Lf,\"checksum\":%.9g}\n",
        BENCHMARK_VERSION,
        kernel,
        threads,
        array_bytes,
        array_bytes * 3U,
        iterations,
        elapsed,
        processed,
        bandwidth,
        checksum
    );
    free(a);
    free(b);
    free(c);
    return isfinite(checksum) ? 0 : 5;
}
