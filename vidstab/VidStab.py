"""VidStab: a class for stabilizing video files"""

import cv2
import numpy as np
import pandas as pd
import imutils.feature.factories as kp_factory
from progress.bar import IncrementalBar


class VidStab:
    """A class for stabilizing video files

    The VidStab class can be used to stabilize videos using functionality from OpenCV.
    Input video is read from file, put through stabilization process, and written to
    an output file.

    The process calculates optical flow (cv2.calcOpticalFlowPyrLK) from frame to frame using
    keypoints generated by the keypoint method specified by the user.  The optical flow will
    be used to generate frame to frame transformations (cv2.estimateRigidTransform).
    Transformations will be applied (cv2.warpAffine) to stabilize video.

    This class is based on the work presented by Nghia Ho at: http://nghiaho.com/?p=2093

    Args:
        kp_method (str): String of the type of keypoint detector to use. Available options:
                           ["GFTT", "BRISK", "DENSE", "FAST", "HARRIS",
                            "MSER", "ORB", "SIFT", "SURF", "STAR"]
        args:            The :class:`FileStorage` instance to wrap
        kwargs:          Keyword arguments for keypoint detector

    Attributes:
        kp_method:       a string naming the keypoint detector being used
        kp_detector:     the keypoint detector object being used
        transforms:      a `pandas.DataFrame` storing the transformations used from frame to frame
    """

    def __init__(self, kp_method='GFTT', *args, **kwargs):
        """instantiate VidStab class

        :param kp_method: String of the type of keypoint detector to use. Available options:
                        ["GFTT", "BRISK", "DENSE", "FAST", "HARRIS",
                         "MSER", "ORB", "SIFT", "SURF", "STAR"]
        :param args: Positional arguments for keypoint detector.
        :param kwargs: Keyword arguments for keypoint detector.
        """
        self.kp_method = kp_method
        # use original defaults in http://nghiaho.com/?p=2093 if GFTT with no additional (kw)args
        if kp_method == 'GFTT' and args == () and kwargs == {}:
            self.kp_detector = kp_factory.FeatureDetector_create('GFTT',
                                                                 maxCorners=200,
                                                                 qualityLevel=0.01,
                                                                 minDistance=30.0,
                                                                 blockSize=3)
        else:
            self.kp_detector = kp_factory.FeatureDetector_create(kp_method, *args, **kwargs)

        self.transforms = None

    def stabilize(self, input_path, output_path, output_fourcc='MJPG', show_progress=True):
        """read video, perform stabilization, & write output to file

        :param input_path: Path to input video to stabilize.
        Will be read with cv2.VideoCapture; see opencv documentation for more info.
        :param output_path: Path to save stabilized video.
        Will be written with cv2.VideoWriter; see opencv documentation for more info.
        :param output_fourcc: FourCC is a 4-byte code used to specify the video codec.
        The list of available codes can be found in fourcc.org.  See cv2.VideoWriter_fourcc documentation for more info.
        :param show_progress: Should a progress bar be displayed to console?
        :return: Nothing is returned.  Output of stabilization is written to `output_path`.

        >>> from vidstab.VidStab import VidStab
        >>> stabilizer = VidStab()
        >>> stabilizer.stabilize(input_path='input_video.mov', output_path='stable_video.avi')

        >>> stabilizer = VidStab(kp_method = 'ORB')
        >>> stabilizer.stabilize(input_path='input_video.mov', output_path='stable_video.avi')
        """
        # set up video capture
        vid_cap = cv2.VideoCapture(input_path)
        frame_count = int(vid_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = int(vid_cap.get(cv2.CAP_PROP_FPS))

        # read first frame
        _, prev_frame = vid_cap.read()
        # convert to gray scale
        prev_frame_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        # get image dims
        (h, w) = prev_frame.shape[:2]

        # initialize storage
        prev_to_cur_transform = []
        if show_progress:
            bar = IncrementalBar('Stabilizing', max=2 * (frame_count - 1))
        # iterate through frame count
        for _ in range(frame_count - 1):
            # read current frame
            _, cur_frame = vid_cap.read()
            # convert to gray
            cur_frame_gray = cv2.cvtColor(cur_frame, cv2.COLOR_BGR2GRAY)
            # detect keypoints
            prev_kps = self.kp_detector.detect(prev_frame_gray)
            prev_kps = np.array([kp.pt for kp in prev_kps], dtype='float32').reshape(-1, 1, 2)
            # calc flow of movement
            cur_kps, status, err = cv2.calcOpticalFlowPyrLK(prev_frame_gray, cur_frame_gray, prev_kps, None)
            # storage for keypoints with status 1
            prev_matched_kp = []
            cur_matched_kp = []
            for i, matched in enumerate(status):
                # store coords of keypoints that appear in both
                if matched:
                    prev_matched_kp.append(prev_kps[i])
                    cur_matched_kp.append(cur_kps[i])
            # estimate partial transform
            transform = cv2.estimateRigidTransform(np.array(prev_matched_kp),
                                                   np.array(cur_matched_kp),
                                                   False)
            if transform is not None:
                # translation x
                dx = transform[0, 2]
                # translation y
                dy = transform[1, 2]
                # rotation
                da = np.arctan2(transform[1, 0], transform[0, 0])
            else:
                dx = dy = da = 0

            # store transform
            prev_to_cur_transform.append([dx, dy, da])
            # set current frame to prev frame for use in next iteration
            prev_frame_gray = cur_frame_gray[:]
            if show_progress:
                bar.next()

        # convert list of transforms to array
        prev_to_cur_transform = np.array(prev_to_cur_transform)
        # cumsum of all transforms for trajectory
        trajectory = np.cumsum(prev_to_cur_transform, axis=0)

        # convert trajectory array to df
        trajectory = pd.DataFrame(trajectory)
        # rolling mean to smooth
        smoothed_trajectory = trajectory.rolling(window=30, center=False).mean()
        # back fill nas caused by smoothing and store
        smoothed_trajectory = smoothed_trajectory.fillna(method='bfill')

        # new set of prev to cur transform, removing trajectory and replacing w/smoothed
        self.transforms = np.array(prev_to_cur_transform + (smoothed_trajectory - trajectory))

        #####
        # APPLY VIDEO STAB
        #####
        # initialize transformation matrix
        transform = np.zeros((2, 3))
        # setup video cap
        vid_cap = cv2.VideoCapture(input_path)
        # setup video writer
        out = cv2.VideoWriter(output_path,
                              cv2.VideoWriter_fourcc(*output_fourcc), fps, (w, h), True)

        # loop through frame count
        for i in range(frame_count - 1):
            # read current frame
            _, frame = vid_cap.read()
            # build transformation matrix
            transform[0, 0] = np.cos(self.transforms[i][2])
            transform[0, 1] = -np.sin(self.transforms[i][2])
            transform[1, 0] = np.sin(self.transforms[i][2])
            transform[1, 1] = np.cos(self.transforms[i][2])
            transform[0, 2] = self.transforms[i][0]
            transform[1, 2] = self.transforms[i][1]
            # apply transform
            transformed = cv2.warpAffine(frame, transform, (w, h))

            # write frame to output video
            out.write(transformed)
            if show_progress:
                bar.next()