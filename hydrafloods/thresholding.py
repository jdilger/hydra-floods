import ee
from ee.ee_exception import EEException
import random
from hydrafloods import geeutils


def bmaxOtsu(collection,
             band=None,
             reductionScale=90,
             initialThreshold=0,
             invert=False,
             gridSize=0.1,
             bmaxThresh=0.75,
             maxBoxes=100,
             seed=7):

    def applyBmax(img):

        def constuctGrid(i):
            def contructXGrid(j):
                j = ee.Number(j)
                box = ee.Feature(ee.Geometry.Rectangle(j,i,j.add(gridSize),i.add(gridSize)))
                out = ee.Algorithms.If(geom.contains(box.geometry(),maxError=1),box,None)
                return ee.Feature(out)
            i = ee.Number(i)
            out = ee.List.sequence(west,east.subtract(gridSize),gridSize).map(contructXGrid)
            return out


        def calcBmax(feature):
            segment = img#.clip(feature)
            initial = segment.lt(initialThreshold)
            p1 = ee.Number(initial.reduceRegion(
                reducer= ee.Reducer.mean(),
                geometry= feature.geometry(),
                bestEffort= True,
                scale= reductionScale,
            ).get(histBand))
            p1 = ee.Number(ee.Algorithms.If(p1,p1,0.99))
            p2 = ee.Number(1).subtract(p1)

            m = segment.updateMask(initial).rename('m1').addBands(
                segment.updateMask(initial.Not()).rename('m2')
            )

            mReduced = m.reduceRegion(
                reducer= ee.Reducer.mean(),
                geometry= feature.geometry(),
                bestEffort= True,
                scale= reductionScale,
            )

            m1 = ee.Number(mReduced.get('m1'))
            m2 = ee.Number(mReduced.get('m2'))

            m1 = ee.Number(ee.Algorithms.If(m1,m1,-25))
            m2 = ee.Number(ee.Algorithms.If(m2,m2,0))

            sigmab = p1.multiply(p2.multiply(m1.subtract(m2).pow(2)))
            sigmat = ee.Number(segment.reduceRegion(
                reducer= ee.Reducer.variance(),
                geometry= feature.geometry(),
                bestEffort= True,
                scale= reductionScale,
            ).get(histBand))
            sigmat = ee.Number(ee.Algorithms.If(sigmat,sigmat,2))
            bmax = sigmab.divide(sigmat)
            return feature.set({'bmax':bmax})

        geom = img.geometry()
        bounds = geom.bounds(maxError=1)
        coords = ee.List(bounds.coordinates().get(0))
        gridRes = ee.Number(gridSize)

        west = ee.Number(ee.List(coords.get(0)).get(0))
        south = ee.Number(ee.List(coords.get(0)).get(1))
        east = ee.Number(ee.List(coords.get(2)).get(0))
        north = ee.Number(ee.List(coords.get(2)).get(1))

        west = west.subtract(west.mod(gridRes))
        south = south.subtract(south.mod(gridRes))
        east = east.add(gridRes.subtract(east.mod(gridRes)))
        north = north.add(gridRes.subtract(north.mod(gridRes)))

        grid = ee.FeatureCollection(
          ee.List.sequence(south,north.subtract(gridRes),gridRes).map(constuctGrid).flatten()
        )

        bmaxes = grid.map(calcBmax).filter(ee.Filter.gt('bmax',bmaxThresh)).randomColumn('random',seed)

        nBoxes = ee.Number(bmaxes.size())
        randomThresh = ee.Number(maxBoxes).divide(nBoxes)
        selection = bmaxes.filter(ee.Filter.lt('random',randomThresh))

        histogram =  img.reduceRegion(ee.Reducer.histogram(255, 1)\
                                    .combine('mean', None, True)\
                                    .combine('variance', None,True),selection,reductionScale,bestEffort=True,
                                    tileScale=16)

        threshold = otsu(histogram.get(histBand.cat('_histogram')))

        water = ee.Image(ee.Algorithms.If(invert,img.gt(threshold),img.lt(threshold)))

        return water.rename('water').uint8()\
            .copyProperties(img)\
            .set('system:time_start',img.get('system:time_start'))

    if band is None:
        collection = collection.select([0])
        histBand = ee.String(ee.Image(collection.first()).bandNames().get(0))

    else:
        histBand = ee.String(qualityBand)
        collection = collection.select(histBand)

    return collection.map(applyBmax)


def edgeOtsu(collection,
             initialThreshold=0,
             canny_threshold=0.05, # threshold for canny edge detection
             canny_sigma=0,        # sigma value for gaussian filter
             canny_lt=0.05,        # lower threshold for canny detection
             smoothing=100,        # amount of smoothing in meters
             connected_pixels=200, # maximum size of the neighborhood in pixels
             edge_length=50,       # minimum length of edges from canny detection
             smooth_edges=100,
             band=None,
             reductionScale=90,
             initThresh=0,
             invert=False,
             seed=7):

    def applyEdge(img):
        # get preliminary water
        binary = img.lt(initialThreshold).rename('binary');

        # get canny edges
        canny = ee.Algorithms.CannyEdgeDetector(binary,canny_threshold,canny_sigma)
        # process canny edges
        connected = canny.mask(canny).lt(canny_lt).connectedPixelCount(connected_pixels, True)
        edges = connected.gte(edge_length)
        edgeBuffer = edges.focal_max(smooth_edges, 'square', 'meters')

        # mask out areas to get histogram for Otsu
        histogram_image = img.updateMask(edgeBuffer)

        histogram =  histogram_image.reduceRegion(ee.Reducer.histogram(255, 2)\
                                    .combine('mean', None, True)\
                                    .combine('variance', None,True),img.geometry(),reductionScale,bestEffort=True,
                                    tileScale=16)

        threshold = otsu(histogram.get(histBand.cat('_histogram')))

        water = ee.Image(ee.Algorithms.If(invert,img.gt(threshold),img.lt(threshold)))

        return water.rename('water').uint8()\
            .copyProperties(img)\
            .set('system:time_start',img.get('system:time_start'))

    if band is None:
        collection = collection.select([0])
        histBand = ee.String(ee.Image(collection.first()).bandNames().get(0))

    else:
        histBand = ee.String(qualityBand)
        collection = collection.select(histBand)

    return collection.map(applyEdge)



def bootstrapOtsu(collection,target_date, reductionPolygons,
                  neg_buffer=-1500,     # negative buffer for masking potential bad data
                  upper_threshold=-14,  # upper limit for water threshold
                  canny_threshold=7,    # threshold for canny edge detection
                  canny_sigma=1,        # sigma value for gaussian filter
                  canny_lt=7,           # lower threshold for canny detection
                  smoothing=100,        # amount of smoothing in meters
                  connected_pixels=200, # maximum size of the neighborhood in pixels
                  edge_length=50,       # minimum length of edges from canny detection
                  smooth_edges=100,
                  qualityBand=None,
                  reverse=False,
                  reductionScale=90):

    tDate = ee.Date(target_date)
    targetColl = collection.filterDate(tDate,tDate.advance(1,'day'))

    nImgs = targetColl.size().getInfo()
    if nImgs <= 0:
        raise EEException('Selected date has no imagery, please try processing another date')

    collGeom = targetColl.geometry()
    polygons = reductionPolygons.filterBounds(collGeom)

    nPolys = polygons.size().getInfo()
    if nPolys > 0:
        ids = ee.List(polygons.aggregate_array('id'))
        random_ids = []
        for i in range(3):
            random_ids.append(random.randint(0, ids.size().subtract(1).getInfo()))
        random_ids = ee.List(random_ids)

        def getRandomIds(i):
            return ids.get(i)

        ids = random_ids.map(getRandomIds)
        polygons = polygons.filter(ee.Filter.inList('id', ids))

        if qualityBand == None:
            target   = targetColl.mosaic().set('system:footprint', collGeom.dissolve())
            target   = target.clip(target.geometry().buffer(neg_buffer))
            smoothed = target.focal_median(smoothing, 'circle', 'meters')
            histBand = ee.String(target.bandNames().get(0))
        else:
            target   = targetColl.qualityMosaic(qualityBand).set('system:footprint', collGeom.dissolve())
            target   = target.clip(target.geometry().buffer(neg_buffer))
            smoothed = target.focal_median(smoothing, 'circle', 'meters')
            histBand = ee.String(qualityBand)

        canny = ee.Algorithms.CannyEdgeDetector(smoothed,canny_threshold,canny_sigma)

        connected = canny.mask(canny).lt(canny_lt).connectedPixelCount(connected_pixels, True)
        edges = connected.gte(edge_length)

        edgeBuffer = edges.focal_max(smooth_edges, 'square', 'meters')

        histogram_image = smoothed.mask(edgeBuffer)
        histogram = histogram_image.reduceRegion(ee.Reducer.histogram(255, 2),polygons.geometry(),reductionScale,bestEffort=True)

        threshold = ee.Number(otsu_function(histogram.get(histBand))).min(upper_threshold)
    else:
        threshold = upper_threshold

    water = smoothed.lt(threshold).clip(geeutils.LAND.geometry())

    return water.rename('water').uint8()\
        .copyProperties(img)\
        .set('system:time_start',img.get('system:time_start'))

def otsu(histogram):
    counts = ee.Array(ee.Dictionary(histogram).get('histogram'))
    means = ee.Array(ee.Dictionary(histogram).get('bucketMeans'))
    size = means.length().get([0])
    total = counts.reduce(ee.Reducer.sum(), [0]).get([0])
    sums = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0])
    mean = sums.divide(total)
    indices = ee.List.sequence(1, size)
    #Compute between sum of squares, where each mean partitions the data.

    def bss_function(i):
        aCounts = counts.slice(0, 0, i)
        aCount = aCounts.reduce(ee.Reducer.sum(), [0]).get([0])
        aMeans = means.slice(0, 0, i)
        aMean = aMeans.multiply(aCounts).reduce(ee.Reducer.sum(), [0]).get([0]).divide(aCount)
        bCount = total.subtract(aCount)
        bMean = sums.subtract(aCount.multiply(aMean)).divide(bCount)
        return aCount.multiply(aMean.subtract(mean).pow(2)).add(
               bCount.multiply(bMean.subtract(mean).pow(2)))

    bss = indices.map(bss_function)
    output = means.sort(bss).get([-1])
    return output
