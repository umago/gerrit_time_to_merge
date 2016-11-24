#!/usr/bin/python

import argparse
import datetime
import json
import subprocess
import sys

import numpy as np
import matplotlib.pyplot as plt


def exec_cmd(command):
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, error = process.communicate()

    return output, error


parser = argparse.ArgumentParser(
    description='Generate a graph depicting how long it took patches to get '
                'merged over time for a given project or a subset of its '
                'contributors.')
parser.add_argument(
    'project',
    help='The OpenStack project to query. For example openstack/neutron.')
parser.add_argument(
    'owner',
    nargs='*',
    help='A list of zero or more Gerrit usernames. For example foo bar.')
args = parser.parse_args()


def get_json_data_from_query(query):
    print query
    data = []
    start = 0

    while True:
        gerrit_cmd = (
            'ssh -p 29418 review.openstack.org gerrit query --current-patch-set --start %(start)s %(query)s --format=json' %
            {'start': start,
             'query': query})
        result, error = exec_cmd(gerrit_cmd)

        if error:
            print error
            sys.exit(1)

        lines = result.split('\n')[:-2]
        data += [json.loads(line) for line in lines]

        if not data:
            print 'No patches found!'
            sys.exit(1)

        print 'Found metadata for %s more patches, %s total so far' % (len(lines), len(data))
        start += len(lines)
        more_changes = json.loads(result.split('\n')[-2])['moreChanges']
        if not more_changes:
            break

    data = sorted(data, key=lambda x: x['createdOn'])
    return data


def get_submission_timestamp(patch):
    try:
        approvals = patch['currentPatchSet']['approvals']  # Not all patches have approvals data
    except KeyError:
        return patch['lastUpdated']

    # Weirdly enough some patches don't have submission data. Take lastUpdated instead.
    return next(
        (approval['grantedOn'] for approval in approvals if approval['type'] == 'SUBM'), patch['lastUpdated'])


def get_loc(patch):
    return max(0, patch['currentPatchSet']['sizeInsertions'] + patch['currentPatchSet']['sizeDeletions'])


def get_color(loc, max_loc):
    """Calculate a color between green and red.
    :param loc: How many lines of code?
    :param max_loc: The value of lines of code over which we return full red
    :return: (r, g, b) tuple
    """
    loc = min(loc, max_loc)  # Patches may have more LOC than the max we calculated, for example 75th percentile.
    return (loc / max_loc,  1.0 - (loc / max_loc), 0)


def get_points_from_data(data):
    points = []

    average_loc = np.percentile([get_loc(patch) for patch in data], 75)
    print 'Average lines of code: %s' % average_loc

    for patch in data:
        creation = datetime.date.fromtimestamp(patch['createdOn'])
        submitted = datetime.date.fromtimestamp(
            get_submission_timestamp(patch))
        x_value = (creation - start).days
        y_value = (submitted - creation).days
        # Gerrit has a weird issue where some old patches have a bogus
        # createdOn value
        if y_value > 0:
            points.append((x_value, y_value, get_color(get_loc(patch), average_loc)))

    return points


def filter_above_percentile(points, percentile):
    percentile = np.percentile([point[1] for point in points], percentile)
    return [point for point in points if point[1] < percentile]


def get_list_of_owners(people):
    people_query = '\('
    for person in people:
        people_query += 'owner:%s OR ' % person
    return '%s\)' % people_query[:-4]


def moving_average(x, n):
    """
    compute an n period moving average.
    Stolen from:
    http://matplotlib.org/examples/pylab_examples/finance_work2.html
    """
    x = np.asarray(x)
    weights = np.ones(n)
    weights /= weights.sum()

    a = np.convolve(x, weights, mode='full')[:len(x)]
    a[:n] = a[n]
    return a


query = "status:merged branch:master project:%s " % args.project
if args.owner:
    query += get_list_of_owners(args.owner)
data = get_json_data_from_query(query)

start = datetime.date.fromtimestamp(data[0]['createdOn'])

points = get_points_from_data(data)

if not points:
    print 'Could not parse points from data. It is likely that the createdOn timestamp of the patches found is bogus.'
    sys.exit(1)

points = filter_above_percentile(points, 95)

x = [point[0] for point in points]
y = [point[1] for point in points]

print 'Average: %s, median: %s' % (
    (int(round(np.average(y))), int(round(np.median(y)))))

plt.xlabel('%s - %s - %s patches' %
           (' '.join(args.owner), args.project, len(data)))
plt.ylabel('Days to merge patch')
plt.grid(axis='y')

# Generate a linear regression line
regression_line = np.polyfit(x, y, 1)
regression_line_function = np.poly1d(regression_line)

averages = moving_average(y, len(x) / 10)

# Plot the data points as well as the regression line
plt.style.use('fivethirtyeight')
plt.plot(x, averages)

colors = [point[2] for point in points]


def to_grey(r, g, b):
    return 0.21 * r + 0.72 * g + 0.07 * b


size = [(1.0 - (to_grey(r, g, b))) * 70 for (r, g, b) in colors]
plt.scatter(x, y, c=colors, s=size, alpha=0.7)

x_axis = range(0, x[-1], max(1, x[-1] / 10))  # 0 to last point, 10 hops

# Generate a date from each hop relative to the date the first patch was
# contributed
x_axis_dates = [
    str(start + datetime.timedelta(days=day_delta)) for day_delta in x_axis]
plt.xticks(x_axis, x_axis_dates, rotation=45)

plt.xlim(xmin=0)
plt.ylim(ymin=0)
plt.show()
