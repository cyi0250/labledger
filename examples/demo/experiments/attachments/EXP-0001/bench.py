"""Time an insertion sort against the built-in sorted() on short shuffled lists; print one CSV row per list length."""
import random
import statistics
import timeit

LENGTHS = (2, 4, 8, 16, 32, 64)
REPEATS = 7  # timing runs per length and function; the median is reported
NUMBER = 2000  # sorts per timing run


def insertion_sort(xs):
    xs = list(xs)
    for i in range(1, len(xs)):
        x, j = xs[i], i - 1
        while j >= 0 and xs[j] > x:
            xs[j + 1] = xs[j]
            j -= 1
        xs[j + 1] = x
    return xs


def median_us(sort, data):
    """Median time of one call of `sort` on `data`, in microseconds."""
    return statistics.median(timeit.repeat(lambda: sort(data), repeat=REPEATS, number=NUMBER)) / NUMBER * 1e6


random.seed(0)
print("n[1],insertion_sort[us],builtin_sorted[us],ratio[1]")
for n in LENGTHS:
    data = random.sample(range(10 * n), n)
    a, b = median_us(insertion_sort, data), median_us(sorted, data)
    print(f"{n},{a:.3f},{b:.3f},{a / b:.2f}")
