
"""Sea Lion Prognostication Engine

https://www.kaggle.com/c/noaa-fisheries-steller-sea-lion-population-count
"""

import sys
import os
from collections import namedtuple
import operator
import glob
import csv 
from math import sqrt
import random

import numpy as np

import PIL
from PIL import Image, ImageDraw, ImageFilter

import skimage
import skimage.io
import skimage.measure
import skimage.feature
import skimage.morphology

import shapely
import shapely.geometry
from shapely.geometry import Polygon

from matplotlib import pyplot as plt
from scipy import ndimage as ndi
import scipy

import cv2


# Notes
# cls -- sea lion class 
# tid -- train, train dotted, or test image id 
# _nb -- short for number
# x, y -- don't forget image arrays organized row, col, channels
#
# With contributions from @bitsofbits ...
#


# ================ Meta ====================
__description__ = 'Sea Lion Prognostication Engine'
__version__ = '0.1.0'
__license__ = 'MIT'
__author__ = 'Gavin Crooks (@threeplusone)'
__status__ = "Prototype"
__copyright__ = "Copyright 2017"

# python -c 'import sealiondata; sealiondata.package_versions()'
def package_versions():
    print('sealionengine \t', __version__)
    print('python        \t', sys.version[0:5])
    print('numpy         \t', np.__version__)
    print('skimage       \t', skimage.__version__)
    print('pillow (PIL)  \t', PIL.__version__)
    print('shapely       \t', shapely.__version__)


SOURCEDIR = os.path.join('..', 'C:\\temp\\sealion')

DATADIR = os.path.join('..', 'C:\\temp\\sealion\\chunks')

VERBOSITY = namedtuple('VERBOSITY', ['QUITE', 'NORMAL', 'VERBOSE', 'DEBUG'])(0,1,2,3)


SeaLionCoord = namedtuple('SeaLionCoord', ['tid', 'cls', 'x', 'y'])


class SeaLionData(object):
    
    def __init__(self, sourcedir=SOURCEDIR, datadir=DATADIR, verbosity=VERBOSITY.NORMAL):
        self.sourcedir = sourcedir
        self.datadir = datadir
        self.verbosity = verbosity
        
        self.cls_nb = 6
        
        self.cls_names = (
            'adult_males',
            'subadult_males',
            'adult_females',
            'juveniles',
            'pups',
            'NOT_A_SEA_LION'
            )
            
        self.cls = namedtuple('ClassIndex', self.cls_names)(*range(0,6))
    
        # backported from @bitsofbits. Average actual color of dot centers.
        self.cls_colors = (
            (243,8,5),          # red
            (244,8,242),        # magenta
            (87,46,10),         # brown 
            (25,56,176),        # blue
            (38,174,21),        # green
			(241,147,6),		# orange
            )
    
            
        self.dot_radius = 3
        
        self.train_nb = 947
        
        self.test_nb = 18636
       
        self.paths = {
            # Source paths
            'sample'     : os.path.join(sourcedir, 'sample_submission.csv'),
            'counts'     : os.path.join(sourcedir, 'Train', 'train.csv'),
            'train'      : os.path.join(sourcedir, 'Train', '{tid}.jpg'),
            'dotted'     : os.path.join(sourcedir, 'TrainDotted', '{tid}.jpg'),
            'test'       : os.path.join(sourcedir, 'Test', '{tid}.jpg'),
            # Data paths
            'coords'     : os.path.join(datadir, 'coords.csv'),  
            }
        
        # From MismatchedTrainImages.txt
        self.bad_train_ids = (
            3, 7, 9, 21, 30, 34, 71, 81, 89, 97, 151, 184, 215, 234, 242, 
            268, 290, 311, 331, 344, 380, 384, 406, 421, 469, 475, 490, 499, 
            507, 530, 531, 605, 607, 614, 621, 638, 644, 687, 712, 721, 767, 
            779, 781, 794, 800, 811, 839, 840, 869, 882, 901, 903, 905, 909, 
            913, 927, 946)
            
        self._counts = None

        
    @property
    def trainshort_ids(self):
        return (0,1,2,4,5,6,8,10)  # Trainshort1
        #return range(41,51)         # Trainshort2
        
    @property 
    def train_ids(self):
        """List of all valid train ids"""
        tids = range(0, self.train_nb)
        tids = list(set(tids) - set(self.bad_train_ids) )  # Remove bad ids
        tids.sort()
        return tids
                    
    @property 
    def test_ids(self):
        return range(0, self.test_nb)
    
    def path(self, name, **kwargs):
        """Return path to various source files"""
        path = self.paths[name].format(**kwargs)
        return path        

    @property
    def counts(self) :
        """A map from train_id to list of sea lion class counts"""
        if self._counts is None :
            counts = {}
            fn = self.path('counts')
            with open(fn) as f:
                f.readline()
                for line in f:
                    tid_counts = list(map(int, line.split(',')))
                    counts[tid_counts[0]] = tid_counts[1:]
            self._counts = counts
        return self._counts

    def rmse(self, tid_counts) :
        true_counts = self.counts
        
        error = np.zeros(shape=[5] )
        
        for tid in tid_counts:
            true_counts = self.counts[tid]
            obs_counts = tid_counts[tid]
            diff = np.asarray(true_counts) - np.asarray(obs_counts)
            error += diff*diff
        #print(error)
        error /= len(tid_counts)
        rmse = np.sqrt(error).sum() / 5
        return rmse 
        

    def load_train_image(self, train_id, border=0, mask=False):
        """Return image as numpy array
         
        border -- add a black border of this width around image
        mask -- If true mask out masked areas from corresponding dotted image
        """
        img = self._load_image('train', train_id, border)
        if mask :
            # The masked areas are not uniformly black, presumable due to 
            # jpeg compression artifacts
            dot_img = self._load_image('dotted', train_id, border).astype(np.uint16).sum(axis=-1)
            img = np.copy(img)
            img[dot_img<40] = 0
        return img
   

    def load_dotted_image(self, train_id, border=0):
        return self._load_image('dotted', train_id, border)
 
 
    def load_test_image(self, test_id, border=0):    
        return self._load_image('test', test_id, border)


    def _load_image(self, itype, tid, border=0) :
        fn = self.path(itype, tid=tid)
        img = np.asarray(Image.open(fn))
        if border :
            height, width, channels = img.shape
            bimg = np.zeros( shape=(height+border*2, width+border*2, channels), dtype=np.uint8)
            bimg[border:-border, border:-border, :] = img
            img = bimg
        return img
    

    def coords(self, train_id):
        """Extract coordinates of dotted sealions and return list of SeaLionCoord objects)"""
        
        # Empirical constants
        MIN_DIFFERENCE = 16
        MIN_AREA = 9
        MAX_AREA = 100
        MAX_AVG_DIFF = 50
        MAX_COLOR_DIFF = 32
       
        src_img = np.asarray(self.load_train_image(train_id, mask=True), dtype = np.float)
        dot_img = np.asarray(self.load_dotted_image(train_id), dtype = np.float)

        img_diff = np.abs(src_img-dot_img)
        
        # Detect bad data. If train and dotted images are very different then somethings wrong.
        avg_diff = img_diff.sum() / (img_diff.shape[0] * img_diff.shape[1])
        if avg_diff > MAX_AVG_DIFF: return None
        
        img_diff = np.max(img_diff, axis=-1)   
           
        img_diff[img_diff<MIN_DIFFERENCE] = 0
        img_diff[img_diff>=MIN_DIFFERENCE] = 255

        sealions = []
        
        for cls, color in enumerate(self.cls_colors):
            # color search backported from @bitsofbits.
            color_array = np.array(color)[None, None, :]
            has_color = np.sqrt(np.sum(np.square(dot_img * (img_diff > 0)[:,:,None] - color_array), axis=-1)) < MAX_COLOR_DIFF 
            contours = skimage.measure.find_contours(has_color.astype(float), 0.5)
            
            if self.verbosity == VERBOSITY.DEBUG :
                print()
                fn = 'diff_{}_{}.png'.format(train_id,cls)
                print('Saving train/dotted difference: {}'.format(fn))
                Image.fromarray((has_color*255).astype(np.uint8)).save(fn)

            for cnt in contours :
                p = Polygon(shell=cnt)
                area = p.area 
                if(area > MIN_AREA and area < MAX_AREA) :
                    y, x= p.centroid.coords[0] # DANGER : skimage and cv2 coordinates transposed?
                    x = int(round(x))
                    y = int(round(y))
                    sealions.append( SeaLionCoord(train_id, cls, x, y) )



        # Start finding negative examples.

        numLions = len(sealions)

        CHUNK_STEP = 120
        CHUNK_SIZE = 92
        max_x = src_img.shape[1]
        max_y = src_img.shape[0]
        #numxcoords = (max_x - CHUNK_STEP // 2) // CHUNK_STEP
        #numycoords = (max_y - CHUNK_STEP // 2) // CHUNK_STEP
        numxcoords = (max_x // CHUNK_STEP) - 1
        numycoords = (max_y // CHUNK_STEP) - 1
        negatives = []
        for j in range(numycoords) :
            for i in range(numxcoords) : 

                xcoord = i * CHUNK_STEP
                ycoord = j * CHUNK_STEP
                overlap = False
                for tid, cls, x, y in sealions :
                    if np.abs(x - xcoord) < CHUNK_STEP and np.abs(y - ycoord) < CHUNK_STEP : 
                        overlap = True
                        break
                if overlap : continue

                    #elif np.abs(lion.x - xcoord) < CHUNK_STEP or np.abs(lion.y - ycoord) < CHUNK_STEP : 
                    #    print("check passed: new: [{xn},{yn}] lion: [{xl},{yl}] dist: [{xd},{yd}]".format(xn=xcoord,yn=ycoord,xl=lion.x,yl=lion.y,xd=np.abs(lion.x-xcoord),yd=np.abs(lion.y-ycoord)))
                    #if (lion.x > xcoord and lion.x < xcoord + CHUNK_STEP) or (lion.y > ycoord and lion.y < ycoord + CHUNK_STEP): continue
                   
            #Add in good results; REMOVE BLACK MASK.
                MIN_AVG_DATA = 200
                neg_img = dot_img[ycoord:ycoord+CHUNK_SIZE,xcoord:xcoord+CHUNK_SIZE,:]
                #neg_img = dot_img[xcoord:xcoord+CHUNK_SIZE,ycoord:ycoord+CHUNK_SIZE,:]
                neg_avg = neg_img.sum() / (neg_img.shape[0] * neg_img.shape[1])
                if neg_avg < MIN_AVG_DATA: continue

                negatives.append( SeaLionCoord(train_id, 5, xcoord ,ycoord ) )

        #add in only one result for each sea lion
        while len(negatives) > len(sealions):
            del negatives[random.randint(0,len(negatives) - 1)]

        sealions = sealions + negatives

        # End neg. examples.


        if self.verbosity >= VERBOSITY.VERBOSE :
            counts = [0,0,0,0,0,0]
            for c in sealions :
                counts[c.cls] +=1
            print()
            print('train_id','true_counts','counted_dots', 'difference', sep='\t')   
            true_counts = self.counts[train_id]
            print(train_id, true_counts, counts, np.array(true_counts) - np.array(counts) , sep='\t' )
          
        if self.verbosity == VERBOSITY.DEBUG :
            img = np.copy(sld.load_dotted_image(train_id))
            r = self.dot_radius
            dy,dx,c = img.shape
            for tid, cls, cx, cy in sealions :                    
                for x in range(cx-r, cx+r+1) : img[cy, x, :] = 255
                for y in range(cy-r, cy+r+1) : img[y, cx, :] = 255    
            fn = 'cross_{}.png'.format(train_id)
            print('Saving crossed dots: {}'.format(fn))
            Image.fromarray(img).save(fn)
     
        return sealions
        

    def save_coords(self, train_ids=None): 
        if train_ids is None: train_ids = self.train_ids
        fn = self.path('coords')
        self._progress('Saving sealion coordinates to {}'.format(fn))
        with open(fn, 'w') as csvfile:
            writer =csv.writer(csvfile)
            writer.writerow( SeaLionCoord._fields )
            for tid in train_ids :
                self._progress()
                for coord in self.coords(tid):
                    writer.writerow(coord)
        self._progress('done')
        
    def load_coords(self):
        fn = self.path('coords')
        self._progress('Loading sea lion coordinates from {}'.format(fn))
        with open(fn) as f:
            f.readline()
            return [SeaLionCoord(*[int(n) for n in line.split(',')]) for line in f]

    
            
    def save_sea_lion_chunks(self, coords, chunksize=128):
        self._progress('Saving image chunks...')
        self._progress('\n', verbosity=VERBOSITY.VERBOSE)
        
        last_tid = -1
        
        for tid, cls, x, y in coords :
            if tid != last_tid:
                img = self.load_train_image(tid, border=chunksize//2, mask=True)
                last_tid = tid

            fn = 'chunk_{tid}_{cls}_{x}_{y}_{size}.png'.format(size=chunksize, tid=tid, cls=cls, x=x, y=y)
            self._progress(' Saving '+fn, end='\n', verbosity=VERBOSITY.VERBOSE)
            Image.fromarray( img[y:y+chunksize, x:x+chunksize, :]).save('.\\chunks\\' + fn)
            self._progress()
        self._progress('done')
        
            
    def _progress(self, string=None, end=' ', verbosity=VERBOSITY.NORMAL):
        if self.verbosity < verbosity: return
        if not string :
            print('.', end='')
        elif string == 'done':
            print(' done') 
        else:
            print(string, end=end)
        sys.stdout.flush()


    def crop_sealion(self, img) :
        """Finds the exact bounds for the sea lion in the chunk."""

        MAX_DISTANCE = 20
        MIN_AREA = 50
        MIN_SIZE = 15

        #img = np.asarray(Image.open(fn))
        gray = skimage.color.rgb2gray(img)
        blurred = ndi.gaussian_filter(gray,3)
        edges = skimage.feature.canny(blurred,2)
        dilated = skimage.morphology.dilation(edges)
        fill = ndi.binary_fill_holes(dilated)
        label_objects, nb_labels = ndi.label(fill)
        
        #if sum == 0, finished. no objects?
        if label_objects.sum() <= 0 :
            return None

        # get center index
        x, y = label_objects.shape
        x //= 2
        y //= 2
        # Find the closest nonzero label to the center.
        tmp = label_objects[x,y]
        label_objects[x,y] = 0
        r,c = np.nonzero(label_objects)
        label_objects[x,y] = tmp
        min_idx = ((r - x)**2 + (c - y)**2).argmin()
        obj = label_objects[r[min_idx], c[min_idx]]
        

        label_objects[label_objects != obj] = 0
        contours = skimage.measure.find_contours(label_objects,0.5)
        
        #Quality Control... cannot find chunk's sealion:
        #Distance from center to Centroid?

        p = Polygon(contours[0])
        c = p.centroid.coords[0]
        dist = np.abs(c[0] - x) + np.abs(c[1] - y) 

        if dist > MAX_DISTANCE :
            return None
        if p.area < MIN_AREA :
            return None

        miny, minx, maxy, maxx = p.bounds
        cimg = img[int(miny):int(maxy),int(minx):int(maxx),:]

        if cimg.shape[0] < MIN_SIZE or cimg.shape[1] < MIN_SIZE :
            return None

        return cimg, c

    def save_sea_lion_chunks_cropped(self, coords, chunksize=128):
        self._progress('Saving image chunks...')
        self._progress('\n', verbosity=VERBOSITY.VERBOSE)
        
        last_tid = -1
        
        for tid, cls, x, y in coords :
            if tid != last_tid:
                img = self.load_train_image(tid, border=chunksize//2, mask=True)
                last_tid = tid

            #skip negative examples.
            if cls == 5 : 
                fn = 'chunk_{tid}_{cls}_{x}_{y}_{size}_{size2}.png'.format(size=chunksize, size2=chunksize, tid=tid, cls=cls, x=x, y=y)
                self._progress(' Saving '+fn, end='\n', verbosity=VERBOSITY.VERBOSE)
                Image.fromarray( img[y:y+chunksize, x:x+chunksize, :]).save('.\\croppedchunks\\' + fn)
                self._progress()
                continue

            chunk = img[y:y+chunksize, x:x+chunksize, :]
            cropped = self.crop_sealion(chunk)
            if cropped is None: 
                fileinfo = 'id: {tid}_{cls}_{x}_{y}'.format(tid=tid, cls=cls, x=x, y=y)
                self._progress(' ----Skipping '+fileinfo, end='\n', verbosity=VERBOSITY.VERBOSE)
                continue

            cimg, c = cropped
            x = int(round(x + (c[1] - chunksize//2)))
            y = int(round(y + (c[0] - chunksize//2)))

            h, w, colors = cimg.shape

            fn = 'chunk_{tid}_{cls}_{x}_{y}_{w}_{h}.png'.format(tid=tid, cls=cls, x=x, y=y, w=w, h=h)
            self._progress(' Saving '+fn, end='\n', verbosity=VERBOSITY.VERBOSE)
            Image.fromarray(cimg).save('.\\croppedchunks\\' + fn)
            self._progress()
        self._progress('done')

# end SeaLionData


##Count sea lion dots and compare to truth from train.csv
sld = SeaLionData()
sld.verbosity = VERBOSITY.VERBOSE
#for tid in sld.trainshort_ids:
#    coord = sld.coords(tid)
#    sld.save_sea_lion_chunks_cropped(coord)
    

#for tid in sld.trainshort_ids:
#    coord = sld.coords(tid)
#    sld.save_sea_lion_chunks(coord, 92)



#Make Cropped Sealions
#Process:
#input                              img = np.asarray(Image.open(fn))
#Gray first?                        gray = skimage.color.rgb2gray(img)
#Blur: Gaussian                     blurred = ndi.gaussian_filter(gray,3)
#Canny Edge                         edges = skimage.feature.canny(blurred,2)
#Dilate Edges (multiple times???)   dilated = skimage.morphology.dilation(edges)
#Fill                               fill = ndi.binary_fill_holes(dilated)
#Remove by coordinates?             label_objects, nb_labels = ndi.label(fill)

#                                   x, y = label_objects.shape
#                                   x //= 2
#                                   y //= 2
#                                   # Find the closest nonzero label to the center.
#                                   tmp = label_objects[x,y]
#                                   label_objects[x,y] = 0
#                                   r,c = np.nonzero(label_objects)
#                                   label_objects[x,y] = tmp
#                                   min_idx = ((r - x)**2 + (c - y)**2).argmin()
#                                   obj = label_objects[r[min_idx], c[min_idx]]

#                                   label_objects[label_objects != obj] = 0
#Find Contours                      contours = skimage.measure.find_contours(label_objects,0.5)
#Get boundaries.



#Find large background rectangles.
#Process:

#Make 
#Mask out all sealion locations.


def show(image) :
    plt.imshow(image)
    plt.show()

def show2(image1, image2) :
    _, (ax1, ax2) = plt.subplots(ncols = 2)
    ax1.imshow(image1)
    ax2.imshow(image2)
    plt.show()

train_id = 1

MIN_DIFFERENCE = 16
MIN_AREA = 9
MAX_AREA = 100
MAX_AVG_DIFF = 50
MAX_COLOR_DIFF = 32
img = np.asarray(sld.load_dotted_image(train_id))
src_img = np.asarray(sld.load_train_image(train_id, mask=True), dtype = np.float)
dot_img = np.asarray(sld.load_dotted_image(train_id), dtype = np.float)

img_diff = np.abs(src_img-dot_img)

img_diff = np.max(img_diff, axis=-1)
img_diff[img_diff<MIN_DIFFERENCE] = 0
img_diff[img_diff>=MIN_DIFFERENCE] = 255
less_noise = skimage.morphology.erosion(img_diff)
contours = skimage.measure.find_contours(less_noise.astype(float), 0.5)

positive_space = np.zeros(less_noise.shape)

for cnt in contours :
    p = Polygon(shell=cnt)
    y, x = p.centroid.coords[0]
    y = int(round(y))
    x = int(round(x))
    #print(x, y)
    #blocksize = 128
    blocksize = 128 // 2
    centersize = 8
    #positive_space[y : y + blocksize, x : x + blocksize] = 255
    #positive_space[y : y + centersize, x : x + centersize] = 80
    positive_space[y - blocksize : y + blocksize, x - blocksize : x + blocksize] = 255
    positive_space[y : y + centersize, x : x + centersize] = 80

#_, (ax1, ax2) = plt.subplots(ncols = 2)
#ax1.imshow(less_noise)
#ax2.imshow(positive_space)
#plt.show()


#has positive mask.


dots_mask = dot_img.astype(np.uint16).sum(axis=-1)

background_space = np.ones(positive_space.shape)
background_space[positive_space > 20] = 0
background_space[dots_mask<40] = 0
                                                   #(3744, 5616)
resize = scipy.misc.imresize(background_space, 1) #(37, 56)
resize[resize < 255] = 0

sq = skimage.morphology.square(3)
run1 = skimage.morphology.erosion(resize,sq)
run2 = skimage.morphology.erosion(run1)

show2(img, run1)

num_runs = 34

for i in range(num_runs) :
    j, i  = run1.nonzero()
    start = random.randint(0, len(i) - 1)
    y, x = i[start], j[start]
    #find largest rectangle.
    #process:
    #get nonzero indexes. random one as starting point
    #build largest possible rectangle from there.
    #skip if too small?
    #store bounds, area
    #label bounds as selected.

    #skip if rectangle too small.
    #exit if num_runs, or if skipped too many times in a row?



