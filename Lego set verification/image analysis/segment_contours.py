'''
    OpenCV: v5.0.0
    numpy: v2.4.6
'''

import sys
import os
from typing import Any, Tuple
import cv2
import numpy as np

def main() -> None:
    '''
    run using: >> python segment_contours.py white_back_legos.png True 53_900
    or run using: >> python segment_contours.py table_legos.png False 5_402
    main(file:str, white_back:str, smallest_area:int)
    file is the name string of the image to segment
    white_back is True if using white background and anything else if using black background.
    smallest_area is the area of the smallest brick in pixels. white_back_legos.png is ~53_900px. table_legos.png is ~5_402px.
    A new folder in this folder will be created that begins with the file name followed by _contour_pieces.
    Be sure to look at the minimized_contours.png image in the new folder to see what gets segmented out.
    '''

    ###GLOBALS###
    file: str = sys.argv[1]
    smallest_area: int = int(sys.argv[3])
    largest_index: int = 0
    image_bw: np.ndarray = np.array([])
    image_wb: np.ndarray = np.array([])
    image_bgr: np.ndarray = np.array([])
    folder: str = ''
    image_threshold: np.ndarray = np.array([])
    contours: Tuple[Any]
    heirarchy: np.ndarray = np.array([])
    outer_contours: Tuple[Any]

    ###SCRIPT###
    image_bw, image_wb, image_bgr = load_images(file)
    folder = create_folder(file)
    if sys.argv[2] == 'True': image_threshold = threshold_image(image_wb, 95)  # best threshold found is 95
    else: image_threshold = threshold_image(image_bw, 95)  # best threshold found is 95
    contours, heirarchy = get_contours(image_threshold)
    draw_contours_image(image_bgr, contours, folder, '_image')
    outer_contours = filter_contours(contours, heirarchy, smallest_area)
    draw_contours_image(image_bgr, outer_contours, folder, '_outer')
    largest_index = find_largest_contour(outer_contours)
    draw_boundary_image(image_bgr, outer_contours, largest_index, folder)
    draw_minimized_boundry_image(image_bgr, outer_contours, largest_index, folder)
    draw_minimized_boundaries_image(image_bgr, outer_contours, folder)
    segment_piece(image_bgr, outer_contours, largest_index, folder)
    segment_all(image_bgr, outer_contours, folder)



###FUNCTIONS###
def load_images(file:str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    '''load file into image arrays'''
    image_bw = cv2.imread(file, cv2.IMREAD_GRAYSCALE)  # cv2.IMREAD_GRAYSCALE is same as 0
    image_wb = cv2.bitwise_not(image_bw)  # if using light background then invert binaries
    image_bgr = cv2.imread(file, 1)  # load image in color to aid in cropping or display

    return image_bw, image_wb, image_bgr


def create_folder(file:str) -> str:
    '''create folder for output images'''
    folder = f'{file.split('.')[0]}_contour_pieces'
    if not os.path.exists(folder): os.mkdir(folder)

    return folder


def threshold_image(image:np.ndarray, thresh:int, opens:int=1) -> np.ndarray:
    '''
    Thresholding creates a binary image from the brightness of the color channels.
    Dilation adds pixels to boundaries to fill gaps and connect nearby objects.
    Erosion shrinks boundaries to remove noise and separate connected objects.
    Adjust these values based on camera position, lighting setup, and to reduce noise.
    '''
    _, image_binary = cv2.threshold(image.copy(), thresh, 255, cv2.THRESH_BINARY)
    kernel = np.ones((7,7), np.uint8)
    # opening the image is image -> erode -> dilate
    for i in range(opens): image_binary = cv2.dilate(cv2.erode(image_binary, kernel), kernel)

    return image_binary

def get_contours(image:np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    '''
    cv2.findContours(image, mode, method) -> contours, hierarchy
    MODES for retrieval and handling of contour hierarchies.
    Contour hierarchies are contours within contours.
    RETR_CCOMP: gets contours and divides them into outer and inner boundaries.
    Every enclosing contour has an external and internal boundary contour.
    RETR_EXTERNAL: gets only externals which may be the border of or outside the image.
    RETR_LIST: gets all contours without creating a hierarchy.
    RETR_TREE: gets all contours and creates a hierarchy.
    METHODS for contour approximation
    CHAIN_APPROX_NONE: literal contour points with no approximation.
    CHAIN_APPROX_SIMPLE: compresses contour points into line enpoints to save memory.
    CHAIN_APPROX_TC89_KCOS: Teh Chin chain approximation.
    CHAIN_APPROX_TC89_L1: Teh Chin chain approximation.
    Heirarchy is [next_same_lvl, previous_same_lvl, 1st_child_index, parent_index]
    '''

    contours, heirarchy = cv2.findContours(
        image,  # image to scan
        cv2.RETR_TREE,  # above MODE
        cv2.CHAIN_APPROX_SIMPLE  # above METHOD
    )

    print(f'{len(contours)} total contours were found')
    return contours, heirarchy

def draw_contours_image(image:np.ndarray, contours:Tuple[Any], folder:str, mod:str='') -> None:
    '''
    The mod:str='' is used to alter the file name. Ideal to preceed with _ underscore character.
    '''
    image_contours:np.ndarray = cv2.drawContours(
        image.copy(),  # image to mutate with contour overlays
        contours,  # contours detected
        -1,  # contour index to display only or -1 for all
        (45, 195, 45),  # color used to draw contours
        10  # thickness of contour lines
    )

    cv2.imwrite(f'{folder}/{len(contours)}{mod}_contours.png', image_contours)

def filter_contours(contours:Tuple[Any], heirarchy:np.ndarray, smallest_area: int) -> Tuple[Any]:
    '''
    Filter out small and child contours (contours within contours) to avoid double detections.
    heirarchy is [next_con, previous_con, parent_con, 1st_child_con] or -1 for None
    smallest_area is converted to perimeter through the approximation
    '''
    outer_contours: Tuple[Any] = []
    smallest_perimeter: int = smallest_area**0.5 * 4 * 0.6  # sqrt to find side length, *4 sides, *0.6 to remove whitespace
    for i, contour in enumerate(contours):
        perimeter = cv2.arcLength(contour, True)
        if (heirarchy[0][i][3]  == -1) and (perimeter > smallest_perimeter): outer_contours.append(contours[i])

    print(f'{len(outer_contours)} pieces were found with area > {smallest_area}')
    return outer_contours

def find_largest_contour(contours:Tuple[Any]) -> int:
    index: int = 0
    biggest: int = 0
    for i, contour in enumerate(contours):
        perimeter = cv2.arcLength(contour, True)
        if perimeter > biggest:
            biggest = perimeter
            index = i

    print(f'Largest contour is contours[{index}] with perimeter of {biggest} pixels')
    return index

def draw_boundary_image(image:np.ndarray, contours:Tuple[Any], index:int, folder:str, mod:str='') -> None:
    '''
    Draws a red box around the contour and saves image into folder.
    The mod:str='' is used to alter the file name. Ideal to preceed with _ underscore character.
    '''
    # find the top left coordinate and size of the bounding rectangle
    x, y, width, height = cv2.boundingRect(contours[index])
    image_contour = cv2.rectangle(  # draw the rectangle on a copy of the image
        image.copy(),
        (x, y),  # top left corner
        (x + width, y + height),  # bottom right corner
        (0,0,255),  # line color used to draw box in image_bgr (0,0,255) is red
        10  # line thickness
    )
    if mod == '': mod = f'_index_{index}'

    cv2.imwrite(f'{folder}/contour{mod}.png', image_contour)

def draw_minimized_boundry_image(image:np.ndarray, contours:Tuple[Any], index:int, folder:str, mod:str='') -> None:
    '''
    Draws a bright green box around the contour or contours and saves image into folder. Box is rotated until area is minimized.
    The mod:str='' is used to alter the file name. Ideal to preceed with _ underscore character.
    '''
    # find the smallest rectangle that the contour will fit in by rotating the rectangle
    rect = cv2.minAreaRect(contours[index])
    # find the coordinates of the smallest rectangle's corners
    box_float = cv2.boxPoints(rect)  # produces float32 values
    # box[0] is bottom right corner and each next index is coordinates of the next corner when going clockwise
    box = np.int32(box_float)  # convert floats to ints to identify specific pixels
    min_rectangle = cv2.drawContours(
        image.copy(),  # image to mutate with contour overlays
        [box],  # vertices of box packed to unpack with next required parameter
        0,  # unpack box vertices required
        (0, 255, 0),  # line color used to draw box in image_bgr (0,255,0) is neon green
        10  # thickness of contour lines
    )
    if mod == '': mod = f'_index_{index}'

    cv2.imwrite(f'{folder}/minimized_contour{mod}.png', min_rectangle)

def draw_minimized_boundaries_image(image:np.ndarray, contours:Tuple[Any], folder:str) -> None:
    '''
    Draws bright green boxes around all contours and saves image into folder.
    Boxes are rotated until area is minimized.
    '''
    image_recursive = image.copy()
    for contour in contours:
        # find the smallest rectangle that the contour will fit in by rotating the rectangle
        rect = cv2.minAreaRect(contour)
        # find the coordinates of the smallest rectangle's corners
        box_float = cv2.boxPoints(rect)  # produces float32 values
        # box[0] is bottom right corner and each next index is coordinates of the next corner when going clockwise
        box = np.int32(box_float)  # convert floats to ints to identify specific pixels
        min_rectangle = cv2.drawContours(
            image_recursive,  # image to mutate with contour overlays
            [box],  # vertices of box packed to unpack with next required parameter
            0,  # unpack box vertices required
            (0, 255, 0),  # line color used to draw box in image_bgr (0,255,0) is neon green
            10  # thickness of contour lines
        )

    cv2.imwrite(f'{folder}/minimized_contours.png', min_rectangle)

def segment_piece(image:np.ndarray, contours:Tuple[Any], index:int, folder:str) -> None:
    '''
    Crops and saves an image of the piece using a minimized boundary square.
    To prevent OpenCV from having to generate out of bounds pixels the image undergoes transformations.
    The image is first padded to be a square of its largest dimension. This prevents piece truncation after transformations.
    The image is second rotated so the boundary square is aligned with the image axes.
    The image is third translated so the center of the piece to be cropped is in the center of the image.
    The second and third steps are mathematically combined into one transformation matrix.
    The coordinates of the center of the piece are also tranformed.
    The piece image array is then extracted from the image and saved in folder.
    '''
    image_h, image_w, _ = image.shape  # unpack image shape for transformations
    center, size, theta = cv2.minAreaRect(contours[index])  # upack rect = ( (center_x,center_y), (piece_w,piece_h), angle )
    piece_w, piece_h = int(size[0]), int(size[1])  # convert to pixel coordinates
    big_dimension = max(image_w, image_h)  # increase image size to avoid going out bounds
    translation_matrix = np.array([  # create matrix to center image on center of piece
        [1,0,image_w / 2 - center[0]],
        [0,1,image_h / 2 - center[1]]
    ], dtype=np.float32)
    rot_matrix = cv2.getRotationMatrix2D(center, theta, 1)  # matrix to align rect with image axes
    # combine rotation and translation matrices into a single transformation matrix
    combined = np.vstack([rot_matrix,[0,0,1]]) @ np.vstack([translation_matrix,[0,0,1]])
    T = combined[:2,:]  # slice combined matrix into affine matrix shape
    new_center = T @ np.array([center[0], center[1], 1])  # apply affine matrix to also tranform piece center
    image_transform = cv2.warpAffine(
        image.copy(),  # image to transform
        T,  # combined and reshaped transformation matrix
        (big_dimension, big_dimension)  # size of output image increased by largest dimension
    )
    piece = cv2.getRectSubPix(  # crop piece from transformed image using transformed center
        image_transform,  # image to crop from
        (piece_w, piece_h),  # piece size
        new_center  # transformed center
    )

    cv2.imwrite(f'{folder}/piece_{index}.png', piece)

def segment_all(image:np.ndarray, contours:Tuple[Any], folder:str) -> None:
    ''' Segments all contours out of image by looping segment_piece() '''
    for i in range(len(contours)): segment_piece(image, contours, i, folder)


###MAIN###
if __name__ == '__main__': main()