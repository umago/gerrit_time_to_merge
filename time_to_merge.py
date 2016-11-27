#!/usr/bin/python

import argparse
from collections import defaultdict
import datetime
import json
import subprocess
import sys

import numpy as np
import matplotlib.pyplot as plt
import pandas
from pystackalytics import Stackalytics


LOC_PERCENTILE = 75


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
    print(query)
    data = []
    start = 0

    while True:
        gerrit_cmd = (
            'ssh -p 29418 review.openstack.org gerrit query --current-patch-set --start %(start)s %(query)s --format=json' %
            {'start': start,
             'query': query})
        result, error = exec_cmd(gerrit_cmd)

        if error:
            print(error)
            sys.exit(1)

        lines = result.split('\n')[:-2]
        data += [json.loads(line) for line in lines]

        if not data:
            print('No patches found!')
            sys.exit(1)

        print('Found metadata for %s more patches, %s total so far' % (len(lines), len(data)))
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
    return (loc / max_loc, 1.0 - (loc / max_loc), 0)


def get_average_loc(lines_of_code):
    return np.percentile(lines_of_code, LOC_PERCENTILE)


def get_points_from_data(data):
    def get_patch_author(patch):
        try:
            return patch['owner']['username']
        except KeyError:  # Not all patches on Gerrit have an owner username interestingly enough
            return

    points = []

    start = datetime.date.fromtimestamp(data[0]['createdOn'])
    average_loc = get_average_loc([get_loc(patch) for patch in data])
    print('Lines of code %s percentile: %s' % (LOC_PERCENTILE, average_loc))

    for patch in data:
        creation = datetime.date.fromtimestamp(patch['createdOn'])
        submitted = datetime.date.fromtimestamp(
            get_submission_timestamp(patch))
        x_value = (creation - start).days
        y_value = (submitted - creation).days

        # Gerrit has a weird issue where some old patches have a bogus
        # createdOn value
        author = get_patch_author(patch)
        if y_value > 0 and author is not None:
            points.append({
                'date': x_value,
                'days_to_merge': y_value,
                'loc': get_loc(patch),
                'author': author})

    return points


def get_list_of_owners(people):
    people_query = '\('
    for person in people:
        people_query += 'owner:%s OR ' % person
    return '%s\)' % people_query[:-4]


def moving_average(data, window):
    return pandas.Series(data).rolling(window=window).mean()


def get_current_figure():
    global CURRENT_FIGURE
    plt.figure(CURRENT_FIGURE)
    CURRENT_FIGURE += 1


def set_fullscreen():
    # http://stackoverflow.com/questions/12439588/how-to-maximize-a-plt-show-window-using-python

    mng = plt.get_current_fig_manager()
    try:
        mng.frame.Maximize(True)
    except AttributeError:
        try:
            mng.window.showMaximized()
        except AttributeError:
            try:
                mng.resize(*mng.window.maxsize())
            except AttributeError:
                pass


def get_figure_title(prefix):
    owners = ' '.join(args.owner) + ' - ' if args.owner else ''
    return prefix + ' - ' + (owners + args.project).replace('/', '_')


def calculate_time_to_merge_figure(points):
    get_current_figure()
    plt.gcf().canvas.set_window_title(get_figure_title('Time to merge'))

    percentile = np.percentile([point['days_to_merge'] for point in points], 95)
    points = [point for point in points if point['days_to_merge'] < percentile]

    x = [point['date'] for point in points]
    y = [point['days_to_merge'] for point in points]

    print('Average days to merge patches: %s, median: %s' % (
          (int(round(np.average(y))), int(round(np.median(y))))))

    plt.xlabel('%s patches' % len(data))
    plt.ylabel('Days to merge patch')
    plt.grid(axis='y')

    window = min(len(x) / 10, 60)
    averages = moving_average(y, window)

    # Plot the patches
    plt.plot(x, averages)

    average_loc = get_average_loc([get_loc(patch) for patch in data])
    colors = [get_color(point['loc'], average_loc) for point in points]

    def to_grey(r, g, b):
        return 0.21 * r + 0.72 * g + 0.07 * b

    size = [(1.0 - (to_grey(r, g, b))) * 70 for (r, g, b) in colors]
    plt.scatter(x, y, s=size, c=colors, alpha=0.75)

    x_axis = range(0, x[-1], max(1, x[-1] / 10))  # 0 to last point, 10 hops

    # Generate a date from each hop relative to the date the first patch was
    # contributed
    start = datetime.date.fromtimestamp(data[0]['createdOn'])
    x_axis_dates = [
        str(start + datetime.timedelta(days=day_delta)) for day_delta in x_axis]
    plt.xticks(x_axis, x_axis_dates, rotation=45)

    plt.xlim(xmin=-5)
    plt.ylim(ymin=-5)
    plt.legend(['Moving mean of the last %s patches' % window, 'Lines of code, small & green to large & red'])
    plt.gcf().subplots_adjust(bottom=0.15)

    set_fullscreen()


def calculate_loc_correlation(points):
    get_current_figure()
    plt.gcf().canvas.set_window_title(get_figure_title('Lines of code'))

    percentile_time = np.percentile([point['days_to_merge'] for point in points], 95)
    percentile_loc = np.percentile([point['loc'] for point in points], 95)
    points = [point for point in points if point['days_to_merge'] < percentile_time and point['loc'] < percentile_loc]

    x = [point['loc'] for point in points]
    y = [point['days_to_merge'] for point in points]

    plt.xlabel('Lines of code')
    plt.ylabel('Days to merge patch')

    plt.scatter(x, y, s=70, alpha=0.75)

    plt.xlim(xmin=-5)
    plt.ylim(ymin=-5)

    set_fullscreen()


def calculate_author_patches_time_to_merge(points):
    get_current_figure()
    plt.gcf().canvas.set_window_title(get_figure_title('Time to merge per author by commits'))

    percentile = np.percentile([point['days_to_merge'] for point in points], 95)
    points = [point for point in points if point['days_to_merge'] < percentile]

    authors = defaultdict(list)  # A map from author to how many days it took to merge each of his/her patches
    for point in points:
        authors[point['author']].append(point['days_to_merge'])

    x = []
    y = []
    for author, patches in authors.items():
        x.append(len(patches))  # How many patches
        y.append(np.average(patches))  # The average of how long it took to merge the patches

    plt.xlabel('Patches by author')
    plt.ylabel('Average days to merge patch per author')
    plt.scatter(x, y, s=70, alpha=0.75)

    plt.xlim(-5, max(x) + 5)
    plt.ylim(-5, max(y) + 5)

    set_fullscreen()


def calculate_author_reviews_time_to_merge(points):
    _calculate_author_time_to_merge_by_metric(points, 'marks')


def calculate_author_emails_time_to_merge(points):
    _calculate_author_time_to_merge_by_metric(points, 'emails')


def _calculate_author_time_to_merge_by_metric(points, metric):
    get_current_figure()
    plt.gcf().canvas.set_window_title(get_figure_title('Time to merge per author by %s') % metric)

    percentile = np.percentile([point['days_to_merge'] for point in points], 95)
    points = [point for point in points if point['days_to_merge'] < percentile]

    authors = defaultdict(list)  # A map from author to how many days it took to merge each of his/her patches
    for point in points:
        authors[point['author']].append(point['days_to_merge'])

    stackalytics = Stackalytics()
    module = args.project.split('/')[-1]

    if metric == 'emails':
        module = None  # When retrieving via emails it looks like Stackalytics tries to find only emails with [module] in the title.
    s_result = stackalytics.engineers(module=module, release='all', metric=metric)['stats']

    if not s_result:
        print 'No result found from Stackalytics API for module %s and metric %s' % (module, metric)
        return

    s_result_by_author = {}
    for item in s_result:
        s_result_by_author[item['id']] = item['metric']

    x = []
    y = []
    for author, patches in authors.items():
        try:
            x.append(s_result_by_author[author])
            y.append(np.average(patches))  # The average of how long it took to merge the patches
        except KeyError:  # People don't always use the same Gerrit and Stackalytics/Launchpad user_ids
            pass

    plt.xlabel('%s by author' % metric)
    plt.ylabel('Average days to merge patch per author')

    plt.scatter(x, y, s=70, alpha=0.75)

    plt.xlim(-5, max(x) + 5)
    plt.ylim(-5, max(y) + 5)

    set_fullscreen()


query = "status:merged branch:master project:%s " % args.project
if args.owner:
    query += get_list_of_owners(args.owner)
data = get_json_data_from_query(query)
points = get_points_from_data(data)

if not points:
    print('Could not parse points from data. It is likely that the createdOn timestamp of the patches found is bogus.')
    sys.exit(1)

plt.style.use('fivethirtyeight')

CURRENT_FIGURE = 1

calculate_time_to_merge_figure(points)
calculate_loc_correlation(points)
calculate_author_patches_time_to_merge(points)
calculate_author_reviews_time_to_merge(points)
calculate_author_emails_time_to_merge(points)
plt.show()
